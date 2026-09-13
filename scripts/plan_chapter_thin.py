#!/usr/bin/env python3
"""Thin one-call chapter planner for the project-owned Qwen3.8 service.

chapter text -> ONE model call -> shots with dialogue, visual and motion prompts.

Hard gates (a failure triggers at most one redo):
  1. source coverage: every shot cites a verbatim excerpt of one of 8
     consecutive chapter segments; every segment is cited or explicitly skipped.
  2. cast/speaker validity: characters, locations and visible speakers are
     StoryBible entries; offscreen speakers are StoryBible names or 无名 roles.
Everything else (density, verbatim ratio, shot count) is reported only.
Executability fixes (long turns, multi-speaker shots, empty shots) are applied
deterministically and never fail.

Outputs (under <output-root>/<novel-id>/<novel-id>_<episode>/):
  segments.json, request_attempt_NN.json, response_attempt_NN.raw.json,
  chapter_script.json, chapter_script_report.json, chapter_script.md,
  episode_plan.json (EpisodePlan for the existing production runtime)
"""
from __future__ import annotations

import argparse
import fcntl
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import httpx

from novel_manga.ingest import read_novel
from novel_manga.models import (
    EpisodePlan,
    ScriptTurn,
    Shot,
    StoryBible,
    TurnDelivery,
    TurnDerivation,
)
from novel_manga.util import atomic_write_json

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thin_profile import endpoint_order, is_fast, load_genre, FRAMES, STYLE_NAME, frame_spec, load_profile

POLICY = "thin-chapter-plan-v13-bounded-repair" + ("-15s" if os.environ.get("NOVEL_CLIP_SECONDS_MAX", "").strip() in {"15", "15.0"} else "")
SEGMENT_COUNT = 8
STAGES_PER_SEGMENT_MAX = 3  # stages one source segment may take in the brief
TURN_MAX_CHARS = 26
QUOTE_MIN_CHARS = 8
QUOTE_MAX_CHARS = 200
MAX_SKIPPED = 0  # every segment must be filmed; run longer instead of dropping story
SHOT_RANGE = (12, 24)
# Clip length budget.  The default (30 s) is what sd2.5 generates in one go;
# NOVEL_CLIP_SECONDS_MAX=15 is the sd2.0 lane, whose reference-to-video mode
# stops at 15 s: twice the clips, half the stages each, same everything else.
CLIP_SECONDS_MAX = float(os.environ.get("NOVEL_CLIP_SECONDS_MAX", "30") or 30)
SHORT_CLIPS = CLIP_SECONDS_MAX <= 15
CLIP_RANGE = (6, 8) if SHORT_CLIPS else (3, 4)
STAGE_RANGE = (2, 3) if SHORT_CLIPS else (4, 6)
MAX_CLIP_SECONDS = 30.0
CLIP_SECONDS_TOLERANCE = 1.0
EPISODE_SECONDS_MAX = 105.0
EPISODE_SECONDS_MIN = 0.0  # set by --min-seconds
SPOKEN_RANGE = (220, 300)
STRICT_PLAN = os.environ.get("NOVEL_PLAN_STRICT", "").strip() == "1"  # spoken budget and quoted lines become redo gates
MIN_SPOKEN_CHARS = 220
ANONYMOUS_SPEAKERS = ["无名测验员", "无名族人", "无名少年", "无名少女", "无名群声"]
SCENE_JOBS = ["建立", "推进", "对峙", "揭示", "反转", "决定", "收束"]
SHOT_SCALES = ["特写", "近景", "中近景", "中景", "全景"]
DELIVERY_MODES = ["visible_dialogue", "offscreen_dialogue", "silent_action", "title_card", "chat_message", "singing"]
CHAT_MAX_CHARS = 36  # the card is drawn by us, so the bubble can hold a full line
SPLIT_PUNCT = "，。！？；：、…—,.!?;:"
STRIP_PUNCT = r"[\s　，。！？；：、…—,.!?;:\"“”'‘’（）()]"
FORBIDDEN_VISUAL = (
    (re.compile(r"(血迹|渗血|血液|流血|鲜血|伤口|破皮)"), "血液或伤口"),
    (re.compile(r"(大字|显示[“\"『「]|写着|字样|刻着[“\"]|显现出[“\"])"), "可读文字"),
)


def stage_seconds(turns: list[dict]) -> float:
    seconds = 1.0
    for turn in turns:
        mode = turn.get("delivery_mode")
        if mode in {"visible_dialogue", "offscreen_dialogue"}:
            seconds += spoken_chars(str(turn.get("text", ""))) / 4.0 + 1.0
        elif mode == "silent_action":
            seconds += 3.0
        elif mode == "chat_message":
            # card mode (the default): the chat card carries the message; the
            # stage itself is one reaction beat
            seconds += 1.0 if CHAT_CARD_MODE else spoken_chars(str(turn.get("text", ""))) / 5.0 + 1.5
        elif mode == "singing":
            seconds += 6.0
    return max(3.0, round(seconds, 2))


def closest_source_line(quote: str, chapter_text: str) -> str:
    """The chapter sentence most similar to a rejected quote, for the repair message."""
    key = quote_key(quote)
    best, best_score = "", 0.0
    for line in re.split(r"(?<=[。！？!?；;])|\n+", chapter_text):
        line = line.strip()
        if len(quote_key(line)) < 4:
            continue
        score = difflib.SequenceMatcher(None, key, quote_key(line)).ratio()
        if score > best_score:
            best, best_score = line, score
    return best[:120]


def trim_quote(quote: str, limit: int = QUOTE_MAX_CHARS) -> str:
    """The longest verbatim prefix whose key fits the limit, cut back to a sentence end when one lies past the halfway point."""
    end = len(quote)
    while end > 0 and len(quote_key(quote[:end])) > limit:
        end -= 1
    head = quote[:end]
    boundary = max(head.rfind(mark) for mark in "。！？；…”」")
    if boundary >= len(head) // 2:
        head = head[:boundary + 1]
    return head.strip()


def chapter_quotes(text: str) -> list[str]:
    """Quoted lines that read like speech.  Novels also quote proper nouns and
    terms (“秘术”“心胜于物”); a quote counts as a line only when it is 8+
    characters or carries sentence punctuation."""
    quotes = re.findall(r"[“\"]([^”\"]{2,120})[”\"]", text)
    return list(dict.fromkeys(quote.strip() for quote in quotes
                              if spoken_chars(quote) >= 8 or (spoken_chars(quote) >= 2 and re.search(r"[，。！？…；、]", quote))))

SYSTEM_PROMPT = """你是中文{frame_text}{style_name}短剧的编剧兼分镜师。把"当前章"改编成一集约90秒的短剧，由{clip_lo}到{clip_hi}段可用视频模型一次生成的连续片段组成，只输出一个JSON对象。
输出结构：clips，{clip_lo}到{clip_hi}段。每段clip在同一地点内连续拍摄，时长{clip_secs_lo}到{clip_secs_hi}秒，由{stage_lo}到{stage_hi}个"阶段"stages组成；每个阶段3到7秒，只有一个主要变化和最多两句台词，写清开始时、主要事件、结束时能直接看到的状态。相邻阶段用不同景别切画面（全景、中景、近景、特写交替）。
时长预算是硬约束：每个发声汉字0.25秒，每句台词加1秒，每个阶段加1秒，无声动作阶段按4秒；单段不得超过{clip_secs_hi}秒，全集不得超过100秒。全集发声字数控制在220到300字之间。
硬规则：
1. 只用当前章的事实、人物和顺序。不得引入后文信息、新事件、新地点，或StoryBible之外的具名角色。
2. 原文已切成{segment_count}个连续区段 seg_1 到 seg_{segment_count}。每个阶段必须写 segment_id，并把该区段里一段连续原文逐字复制到 source_quote（8到120字；不得改字、不得拼接）。每个区段都必须至少被一个阶段引用，一个都不许跳过；skipped_segments 必须是空数组 []。每个区段用1到{stages_per_segment}个阶段带过：内容多的区段把对话压成一两句、把过程并成一个阶段，也不能整段不拍。
3. 成片没有旁白、没有内心独白。可听的只有四种：visible_dialogue（画内可见说话者，一个阶段只允许一个可见说话者）、offscreen_dialogue（画外声：群众议论、测验员喊话等）、silent_action（无声的可见动作或反应，text写动作）、title_card（时间或地点跳转的字幕卡，只在必要时用）。另有一种不发声的 chat_message：手机或电脑屏幕上显示的聊天消息，speaker_name 写发消息的人，text 写消息原文，逐字取自原文、不超过36字（更长的只取到一个标点为止）；一个阶段最多八条（消息由插卡呈现，一个阶段可以带一整轮对话，不必为了拆消息而多写阶段）；群聊消息的 chat_target 留空；一对一私聊的消息把 chat_target 写成和主角私聊的那个人的名字——绝不能写主角自己，同一段私聊里每条消息（无论谁发的）都写同一个名字；私聊是两个人来回说话：对方发的消息 speaker_name 要写对方的名字，只有主角自己发的才写主角，不要把整段私聊都记成主角发的；同一阶段不要混用群聊和私聊。屏幕上的聊天界面由后期插卡渲染，画面里不需要拍清屏幕文字，含 chat_message 的阶段 start_state 和 event 只写看手机的人的动作与反应。原文里凡是聊天软件上的消息（形如「昵称：内容」的对话、群里的喊话、私聊），必须用 chat_message 呈现，一条都不许改成画外音、旁白或角色自己念出来。唱歌场景用 singing：speaker_name 写唱歌的人，text 只写演唱方式（如"轻声哼唱一段温柔的无词旋律"），绝不写任何歌词、歌名或已有歌曲，观众的反应用其他阶段的画面和画外音表现。silent_action只能写此刻能拍到的动作，不能用来表达回忆、心理活动、气质评价或规则说明。
4. 台词取舍：推动剧情和人物关系的原文台词必须保留，可以只删子句、不改词序；重复表达同一意思的群众议论要合并成一两句或删掉。叙述里承载来历、规则和身份的信息（谁曾经是什么、某条规则意味着什么、某个称号指谁）用一两句无名族人的画外议论或角色问答说出来，改成口语但不新增原文没有的事实。内心独白不要改成出声自语，改成可见反应。
5. 每条turn的text不超过26个汉字，长句拆成多条turn。
6. 阶段字段：start_state写开始时画面（谁在哪、站位、朝向、表情、道具）；event写这几秒内的一个主要动作或事件；end_state写结束时能直接看到的状态（人物位置、朝向、表情、道具归属）；sfx写环境声或动作音效（如"人群低语""脚步声"），没有就空字符串，不要写"寂静声""注视声"这类不是声音的词；shot_scale写景别。情绪一律写成可见表现（眼神、眉头、嘴角、呼吸、手部动作），不写"气质如清莲""闪过一丝痛苦"这类拍不出来的词。不描述镜头运动、文字、字幕、Logo。相邻阶段不要重复同一个开始画面。
   camera写摄影机的物理位置，像一个在场的目击者：站在这个空间的哪里（灵碑侧后方、大厅长桌尽头、门框外、人群缝隙里）、离主体多远、高度是平视还是略低略高、前景有没有自然遮挡；摄影机静止，不写推拉摇移。
   light先写真实光源再写效果：主光源是什么、从哪个方向来（月光从左上、案头油灯在右侧、灵碑纹路的金光从下方），次光源是什么，阴影落在哪里，冷暖关系如何；同一段内光源不能凭空改变，不用"电影感""氛围感"这类词。
   camera和light各不超过40个汉字。同一段clip里光源不变时，后续阶段的light直接写"同上"；机位不变时camera也可写"同上"。
   每段clip写avoid：本段具体不要出现的东西，用名词，例如"灵碑上不要出现可读文字""大厅不要出现现代家具""不要给楚焱红色发光的眼睛"；不写"低质量"这类空泛负面词。clip_id只写clip_1这样的短编号。
7. 画面描述不得出现血液、伤口、破皮、流血。灵碑、石碑、牌匾、纸张上不得出现可读文字或数字，一律写成"无字的发光纹路"；唯一允许的可读文字是手机或电脑屏幕上的聊天消息（用 chat_message 给出内容）。
8. clip.characters只填该段画面中出现的StoryBible具名角色；location只填给定地点名。speaker_name是具名角色，或"无名测验员""无名族人"这类无名画外角色；无名角色只能用offscreen_dialogue。silent_action和title_card的speaker_name留空字符串。
9. 只输出JSON。不要Markdown、不要解释、不要代码围栏。"""


def render_brief(prompt: str) -> str:
    """Fill the brief's clip and stage numbers from the ranges in force for this run."""
    prompt = prompt + separate_clause()
    # CLIP_SECONDS_MAX is this lane's cap (15 s on sd2.0); MAX_CLIP_SECONDS is the model's
    # own 30 s ceiling.  The brief has to quote the lane's, or a 15 s lane is invited to
    # film for 30 s and the packer has to cut the result apart.
    low = 10 if SHORT_CLIPS else 20
    return (prompt.replace("{clip_lo}", str(CLIP_RANGE[0])).replace("{clip_hi}", str(CLIP_RANGE[1]))
            .replace("{stage_lo}", str(STAGE_RANGE[0])).replace("{stage_hi}", str(STAGE_RANGE[1]))
            .replace("{clip_secs_lo}", str(low)).replace("{clip_secs_hi}", str(int(CLIP_SECONDS_MAX)))
            .replace("{segment_count}", str(SEGMENT_COUNT)).replace("{stages_per_segment}", str(STAGES_PER_SEGMENT_MAX)))


PROMPT_EXAMPLE_DEFAULTS = {
    "light": "（月光从左上、案头油灯在右侧、灵碑纹路的金光从下方）",
    "avoid": "例如\"灵碑上不要出现可读文字\"\"大厅不要出现现代家具\"\"不要给楚焱红色发光的眼睛\"；",
    "text_props": "灵碑、石碑、牌匾、纸张上不得出现可读文字或数字，一律写成\"无字的发光纹路\"；",
    "anon": "或\"无名测验员\"\"无名族人\"这类无名画外角色",
    "offscreen": "（画外声：群众议论、测验员喊话等）",
    "camera": "（灵碑侧后方、大厅长桌尽头、门框外、人群缝隙里）",
    "narrator": "用一两句无名族人的画外议论"
}  # the brief's built-in examples; genre files override

def analysis_instruction() -> str:
    """Rendered at call time: the fast tier changes CLIP_RANGE after import."""
    return (
        "先做内部规划，不要输出JSON：逐区段列出必须保留的引号台词、必须外化成台词的叙述事实（写出改成谁说的什么话）、"
        "此刻可拍的动作；然后给出片段划分：每段覆盖哪些区段、几个阶段、估算秒数，"
        f"总共{CLIP_RANGE[0]}到{CLIP_RANGE[1]}段。不超过800字。"
    )


SEPARATE_MIN_FAILURES = 100
SEPARATE_MIN_RATE = 0.5
SEPARATE_MAX_PAIRS = 3
SEPARATE_PAIRS: list[tuple[str, str]] = []  # filled by load_separate_pairs() in main()


def load_separate_pairs(novel_dir: Path) -> list[tuple[str, str]]:
    """Character pairs this novel's review record says the generator cannot tell apart."""
    path = novel_dir / "confusable_pairs.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    pairs = [tuple(entry["pair"]) for entry in data.get("pairs", [])
             if entry.get("failed", 0) >= SEPARATE_MIN_FAILURES and entry.get("rate", 0) >= SEPARATE_MIN_RATE]
    return pairs[:SEPARATE_MAX_PAIRS]


def separate_clause() -> str:
    """The brief's rule about those pairs, empty when the novel has none."""
    if not SEPARATE_PAIRS:
        return ""
    listed = "、".join(f"{a}与{b}" for a, b in SEPARATE_PAIRS)
    return ("\n\n【同框限制】以下角色对不要出现在同一个阶段的画面里：" + listed + "。"
            "这几对角色在成片里反复被画成同一个人，所以同场时只让其中一个入画，另一个用 offscreen_dialogue 说话、"
            "或者写成刚离开、在画外、背对镜头看不见脸；需要两人交替说话就拆成前后两个阶段，各拍一个。"
            "这条只约束画面里同时出现谁，不改变剧情、台词内容和顺序。")


def separation_warnings(shots: list[dict]) -> list[str]:
    """Stages that still put a forbidden pair on screen together."""
    if not SEPARATE_PAIRS:
        return []
    out = []
    for index, shot in enumerate(shots, start=1):
        visible = {turn.get("speaker_name") for turn in shot.get("turns") or []
                   if turn.get("delivery_mode") == "visible_dialogue"}
        text = " ".join(str(shot.get(key) or "") for key in ("start_state", "event", "end_state"))
        for a, b in SEPARATE_PAIRS:
            on_screen = {name for name in (a, b) if name in visible or name in text}
            if len(on_screen) == 2:
                out.append(f"阶段{index}：{a} 与 {b} 同时入画（这两个角色容易被画成同一个人）")
    return out


def compact(value: str) -> str:
    return re.sub(r"[\s　]+", "", value or "")


def quote_key(value: str) -> str:
    """Provenance key: letters, digits and CJK only.

    The gate exists to prove the words come from the chapter.  Quotation
    marks, a colon turned into a full stop, or a dropped ellipsis are not
    evidence of invention, so all punctuation and whitespace are ignored.
    """
    return "".join(re.findall(r"[A-Za-z0-9\u3400-\u9fff]", value or ""))


def spoken_chars(value: str) -> int:
    return len(re.sub(STRIP_PUNCT, "", value or ""))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def split_segments(text: str, title: str, count: int) -> list[dict]:
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    if paragraphs and compact(paragraphs[0]) == compact(title):
        paragraphs = paragraphs[1:]
    total = sum(len(compact(p)) for p in paragraphs)
    segments: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        current.append(paragraph)
        current_len += len(compact(paragraph))
        remaining = count - len(segments)
        remaining_chars = total - sum(len(compact(p)) for group in segments for p in group) - current_len
        if remaining > 1 and current_len >= (current_len + remaining_chars) / remaining:
            segments.append(current)
            current, current_len = [], 0
    if current:
        segments.append(current)
    return [
        {"segment_id": f"seg_{index}", "text": "\n".join(group), "chars": sum(len(compact(p)) for p in group)}
        for index, group in enumerate(segments, start=1)
    ]


def split_turn_text(text: str) -> list[str]:
    text = text.strip()
    if spoken_chars(text) <= TURN_MAX_CHARS:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if spoken_chars(remaining) <= TURN_MAX_CHARS:
            chunks.append(remaining)
            break
        cut = None
        counted = 0
        for position, character in enumerate(remaining):
            if not re.match(STRIP_PUNCT, character):
                counted += 1
            if counted > TURN_MAX_CHARS:
                break
            if character in SPLIT_PUNCT and counted >= 4:
                cut = position + 1
        if cut is None:
            counted = 0
            for position, character in enumerate(remaining):
                if not re.match(STRIP_PUNCT, character):
                    counted += 1
                if counted >= TURN_MAX_CHARS:
                    cut = position + 1
                    break
            cut = cut or len(remaining)
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    merged: list[str] = []
    for chunk in chunks:
        if merged and spoken_chars(chunk) == 0:
            merged[-1] += chunk
        elif chunk:
            merged.append(chunk)
    return [chunk for chunk in merged if spoken_chars(chunk) > 0]


def build_schema(character_names: list[str], location_names: list[str], segment_ids: list[str]) -> dict:
    turn = {
        "type": "object",
        "additionalProperties": False,
        "required": ["speaker_name", "delivery_mode", "text", "emotion", "chat_target"],
        "properties": {
            "speaker_name": {"type": "string", "enum": [*character_names, *ANONYMOUS_SPEAKERS, ""]},
            "delivery_mode": {"type": "string", "enum": DELIVERY_MODES},
            "text": {"type": "string"},
            "emotion": {"type": "string"},
            "chat_target": {"type": "string", "enum": [*character_names, ""]},  # chat_message only: empty = group chat, a name = a one-to-one chat
        },
    }
    stage = {
        "type": "object",
        "additionalProperties": False,
        "required": ["segment_id", "source_quote", "start_state", "event", "end_state", "camera", "light", "sfx", "shot_scale", "turns"],
        "properties": {
            "segment_id": {"type": "string", "enum": segment_ids},
            "source_quote": {"type": "string"},
            "start_state": {"type": "string"},
            "event": {"type": "string"},
            "end_state": {"type": "string"},
            "camera": {"type": "string"},
            "light": {"type": "string"},
            "sfx": {"type": "string"},
            "shot_scale": {"type": "string", "enum": SHOT_SCALES},
            "turns": {"type": "array", "minItems": 1, "maxItems": 8, "items": turn},
        },
    }
    clip = {
        "type": "object",
        "additionalProperties": False,
        "required": ["clip_id", "location", "characters", "avoid", "stages"],
        "properties": {
            "clip_id": {"type": "string"},
            "location": {"type": "string", "enum": location_names},
            # capped: an uncapped array let the model repeat one name until the token budget ran out
            "characters": {"type": "array", "maxItems": 6, "items": {"type": "string", "enum": character_names}},
            "avoid": {"type": "string"},
            "stages": {"type": "array", "minItems": 1, "maxItems": 6, "items": stage},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["video_title", "hook", "summary", "clips", "skipped_segments"],
        "properties": {
            "video_title": {"type": "string"},
            "hook": {"type": "string"},
            "summary": {"type": "string"},
            "clips": {"type": "array", "minItems": 1, "maxItems": 10, "items": clip},
            "skipped_segments": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["segment_id", "reason"],
                    "properties": {
                        "segment_id": {"type": "string", "enum": segment_ids},
                        "reason": {"type": "string"},
                    },
                },
            },
        },
    }


def flatten_clips(raw: dict) -> list[dict]:
    """Turn model clips/stages into the flat shot list the gates and packer use."""
    shots: list[dict] = []
    for clip_number, clip in enumerate(raw.get("clips") or [], start=1):
        if not isinstance(clip, dict):
            continue
        raw_id = str(clip.get("clip_id") or "").strip()
        # The id is a free string in the schema; a model once dumped its whole
        # planning draft into it.  Only accept a short token, else renumber.
        clip_id = raw_id if re.fullmatch(r"[A-Za-z0-9_\-]{1,24}", raw_id) else f"clip_{clip_number:02d}"
        for stage_number, stage in enumerate(clip.get("stages") or [], start=1):
            if not isinstance(stage, dict):
                continue
            shots.append(
                {
                    "clip_hint": clip_id,
                    "label": f"{clip_id} stage {stage_number}",
                    "location": clip.get("location", ""),
                    "characters": list(clip.get("characters") or []),
                    "segment_id": stage.get("segment_id", ""),
                    "source_quote": stage.get("source_quote", ""),
                    "visual_prompt": stage.get("start_state", ""),
                    "motion_prompt": stage.get("event", ""),
                    "end_state": stage.get("end_state", ""),
                    "camera": stage.get("camera", ""),
                    "light": stage.get("light", ""),
                    "avoid": clip.get("avoid", ""),
                    "sfx": stage.get("sfx", ""),
                    "shot_scale": stage.get("shot_scale", "中近景"),
                    "turns": list(stage.get("turns") or []),
                }
            )
    return shots


CAST_RECENT_CHAPTERS = 3  # a character on screen this recently stays offered even when this chapter does not name them


def cast_history(novel_dir: Path) -> dict:
    """{"characters": {name: [chapters]}, "locations": {...}} for this novel.

    Built from the scripts already written when the file is missing, so an
    existing novel does not need a migration step.
    """
    path = novel_dir / "cast_index.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    index: dict[str, dict[str, list[int]]] = {"characters": {}, "locations": {}}
    for script_path in sorted(novel_dir.glob(f"{novel_dir.name}_*/chapter_script.json")):
        try:
            script = json.loads(script_path.read_text(encoding="utf-8"))
            chapter = int(script.get("episode_index") or script_path.parent.name.rsplit("_", 1)[1])
        except (OSError, ValueError, IndexError):
            continue
        for shot in script.get("shots", []):
            for name in shot.get("characters", []) or []:
                index["characters"].setdefault(str(name), []).append(chapter)
            if shot.get("location"):
                index["locations"].setdefault(str(shot["location"]), []).append(chapter)
    for group in index.values():
        for name, chapters in group.items():
            group[name] = sorted(set(chapters))
    return index


def recent_names(group: dict, chapter: int, window: int) -> set[str]:
    return {name for name, chapters in group.items() if any(chapter - window <= int(c) < chapter for c in chapters)}


def record_cast(novel_dir: Path, chapter: int, characters: list[str], locations: list[str]) -> None:
    """Record who and where this chapter shows, replacing what an earlier plan of it recorded (planners may run
    in parallel).  Adding only kept a character a re-written chapter no longer has as "seen lately" for the next
    three chapters (星海 14 and 诸天 101 entries disagreed with the scripts on 2026-09-11)."""
    path = novel_dir / "cast_index.json"
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index = cast_history(novel_dir)
        for group in index.values():
            for name in list(group):
                group[name] = [c for c in group[name] if int(c) != chapter]
                if not group[name]:
                    del group[name]
        for key, values in (("characters", characters), ("locations", locations)):
            for name in values:
                if not name:
                    continue
                chapters = set(index[key].setdefault(str(name), []))
                chapters.add(chapter)
                index[key][str(name)] = sorted(chapters)
        atomic_write_json(path, index)


UNCITED_ERROR = re.compile(r"^(seg_\d+) is neither cited by any shot")
STAGE_ERROR = re.compile(r"^([A-Za-z0-9_\-]{1,24} stage \d+): ")
CLIP_LEVEL_ERROR = re.compile(r"characters not in StoryBible|unknown location")
PATCH_ROUNDS = 3  # small repair calls per chapter before a full re-plan is the only option left
PATCH_TIMEOUT_SECONDS = 120.0
PATCH_TOTAL_SECONDS = 180.0  # shared across all repair rounds and full-draft attempts


def patchable_errors(errors: list[str]) -> tuple[list[str], dict[str, list[str]]] | None:
    """Split gate errors into forgotten segments and faulty stages.

    Returns None when any error needs the whole plan rewritten: nothing
    returned, the length floor, a clip-level location or cast problem.
    """
    missing_ids: list[str] = []
    faulty: dict[str, list[str]] = {}
    for error in errors:
        uncited = UNCITED_ERROR.match(error)
        local = STAGE_ERROR.match(error)
        if uncited:
            missing_ids.append(uncited.group(1))
        elif local and not CLIP_LEVEL_ERROR.search(error):
            faulty.setdefault(local.group(1), []).append(error[local.end():])
        else:
            return None
    return (list(dict.fromkeys(missing_ids)), faulty) if missing_ids or faulty else None


def stage_slots(raw: dict) -> dict[str, tuple[int, int]]:
    """label -> (clip index, stage index), numbered exactly like flatten_clips."""
    slots: dict[str, tuple[int, int]] = {}
    for clip_number, clip in enumerate(raw.get("clips") or [], start=1):
        if not isinstance(clip, dict):
            continue
        raw_id = str(clip.get("clip_id") or "").strip()
        clip_id = raw_id if re.fullmatch(r"[A-Za-z0-9_\-]{1,24}", raw_id) else f"clip_{clip_number:02d}"
        for stage_number, stage in enumerate(clip.get("stages") or [], start=1):
            if isinstance(stage, dict):
                slots[f"{clip_id} stage {stage_number}"] = (clip_number - 1, stage_number - 1)
    return slots


def patch_plan(raw: dict, missing_ids: list[str], faulty: dict[str, list[str]], segments: list[dict], names: list[str], locations: list[str], *, timeout: float = PATCH_TIMEOUT_SECONDS) -> dict:
    """Repair a plan with one small call instead of a 150-650 s re-plan.

    The model sees the clip outline, the forgotten segments' text and the
    rejected stages with their errors and source text; it returns only the
    stages to insert and the replacements for the rejected ones.  The result
    is a deep copy; the caller validates it like any draft.
    """
    from thin_review import ask_json
    segment_ids = [s["segment_id"] for s in segments]
    texts = {s["segment_id"]: s["text"] for s in segments}
    slots = stage_slots(raw)
    faulty = {label: errs for label, errs in faulty.items() if label in slots}
    clip_ids = [str(c.get("clip_id")) for c in raw.get("clips", []) if isinstance(c, dict)]
    if not clip_ids or not (missing_ids or faulty):
        raise ValueError("nothing to patch")
    stage_of = lambda ids: build_schema(names, locations, ids)["properties"]["clips"]["items"]["properties"]["stages"]["items"]  # noqa: E731
    outline = []
    for clip in raw.get("clips", []):
        stages = clip.get("stages") or []
        outline.append({
            "clip_id": clip.get("clip_id"), "location": clip.get("location"), "characters": clip.get("characters"),
            "stages": [{"n": i, "segment_id": s.get("segment_id"), "event": str(s.get("event", ""))[:60]} for i, s in enumerate(stages, start=1)],
        })
    schema = {
        "type": "object", "additionalProperties": False, "required": ["insertions", "replacements"],
        "properties": {
            "insertions": {"type": "array", "minItems": len(missing_ids), "maxItems": len(missing_ids), "items": {
                "type": "object", "additionalProperties": False, "required": ["clip_id", "after_stage", "stage"],
                "properties": {"clip_id": {"type": "string", "enum": clip_ids}, "after_stage": {"type": "integer", "minimum": 0},
                               "stage": stage_of(missing_ids or segment_ids)}}},
            "replacements": {"type": "array", "minItems": len(faulty), "maxItems": len(faulty), "items": {
                "type": "object", "additionalProperties": False, "required": ["label", "stage"],
                "properties": {"label": {"type": "string", "enum": list(faulty) or ["-"]}, "stage": stage_of(segment_ids)}}},
        },
    }
    parts = ["下面是一集短剧的分镜大纲。只输出需要修改的部分，不要改动其他阶段。"]
    if missing_ids:
        parts.append(f"原文里有 {len(missing_ids)} 个区段还没有任何阶段引用（{'、'.join(missing_ids)}）。为每个遗漏区段补写恰好一个阶段，插进最合适的 clip"
                     "（after_stage 是插在该 clip 第几个阶段之后，0 表示放在最前）；新阶段的 segment_id 必须是该遗漏区段。")
    if faulty:
        parts.append(f"另有 {len(faulty)} 个阶段没过硬门检查，逐个重写整个阶段（label 原样填回，segment_id 不变），只修错误指出的问题，其余内容尽量保持。")
    parts.append("规则：source_quote 从该区段原文逐字复制 8 到 120 字；画面描述不得出现血液、伤口、破皮、流血，碑上的结果写成无字的发光纹路；"
                 "offscreen_dialogue 和 chat_message 必须写 speaker_name；台词从原文取；只用给出的人物名，格式和已有阶段一致。")
    parts.append(f"分镜大纲：{json.dumps(outline, ensure_ascii=False)}")
    parts.append(f"可用人物：{names}")
    shown: list[str] = list(missing_ids)
    for label, errs in faulty.items():
        clip_index, stage_index = slots[label]
        stage = raw["clips"][clip_index]["stages"][stage_index]
        parts.append(f"问题阶段 {label}\n错误：" + " | ".join(errs) + f"\n当前内容：{json.dumps(stage, ensure_ascii=False)}")
        shown.append(str(stage.get("segment_id")))
    for sid in dict.fromkeys(shown):
        if sid in texts:
            parts.append(f"区段 {sid} 原文：\n{texts[sid][:1800]}")
    budget = min(6000, 800 + 900 * (len(missing_ids) + len(faulty)))
    verdict = ask_json([{"type": "text", "text": "\n\n".join(parts)}], schema, name="plan_patch", max_tokens=budget, timeout=timeout, retry_truncated=False)
    patched = json.loads(json.dumps(raw, ensure_ascii=False))
    for item in verdict.get("replacements", []):  # replacements first: insertions shift stage numbers
        slot = slots.get(str(item.get("label")))
        if slot and isinstance(item.get("stage"), dict):
            patched["clips"][slot[0]]["stages"][slot[1]] = item["stage"]
    by_id = {str(c.get("clip_id")): c for c in patched.get("clips", [])}
    for item in verdict.get("insertions", []):
        clip = by_id.get(str(item.get("clip_id"))) or patched["clips"][-1]
        stages = clip.setdefault("stages", [])
        position = max(0, min(int(item.get("after_stage", len(stages))), len(stages)))
        stages.insert(position, item["stage"])
    return patched


def compact_bible(bible: StoryBible, location_map: dict[str, str]) -> dict:
    return {
        "novel_title": bible.novel_title,
        "genre": bible.genre,
        "characters": [
            {
                "name": character.name,
                "role": character.role,
                "gender": character.gender,
                "age": character.age,
                "appearance": character.appearance[:120],
            }
            for character in bible.characters
        ],
        "locations": [{"name": short, "description": full} for short, full in location_map.items()],
        "continuity_rules": bible.continuity_rules,
    }


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("model must return one JSON object")
    return value


def _post_any(client: httpx.Client, base_urls: list[str], headers: dict, request: dict) -> dict:
    """Try the preferred endpoint, then the others; a dead instance costs one connect error."""
    last: Exception | None = None
    for base_url in base_urls:
        try:
            return _post(client, base_url, headers, request)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as error:
            last = error
            continue
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (502, 503, 504):
                last = error
                continue
            raise
    assert last is not None
    raise last


def _post(client: httpx.Client, base_url: str, headers: dict, request: dict) -> dict:
    if os.environ.get("QWEN38_LOCAL_STREAM", "").strip() == "1":
        from thin_review import stream_completion  # a proxied platform cuts non-streaming calls at 60 s
        return stream_completion(client, f"{base_url.rstrip('/')}/chat/completions", headers, request)
    response = client.post(f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=request)
    response.raise_for_status()
    return response.json()


def grammar_text(grammar: dict | None) -> str:
    if not grammar:
        return ""
    axes = [
        ("光影与对比", grammar.get("light_contrast")),
        ("色彩与曝光", grammar.get("color_exposure")),
        ("镜头与机位", grammar.get("lens_camera")),
        ("构图与空间", grammar.get("composition_space")),
    ]
    body = "；".join(f"{label}：{value}" for label, value in axes if value)
    rejects = "；".join(str(item) for item in grammar.get("rejects", []) if item)
    times = grammar.get("location_time") or {}
    time_text = "；".join(f"{place}：{when}" for place, when in times.items())
    return (
        body
        + (f"。全书禁忌：{rejects}" if rejects else "")
        + (f"。各地点的时间与主光源已由地点卡锁定，light必须照此写：{time_text}" if time_text else "")
    )


def qwen_default() -> str:
    return "__all__"


def call_model(*, base_url: str, model: str, payload: dict, schema: dict, max_tokens: int, timeout: float, analysis_tokens: int = 2500, notes: str = "", grammar: dict | None = None, profile: dict | None = None, fast: bool = False) -> tuple[str, dict]:
    frame = frame_spec(profile) if profile else FRAMES["9:16"]
    system_prompt = render_brief(SYSTEM_PROMPT).replace("{frame_text}", frame["text"]).replace("{style_name}", STYLE_NAME[(profile or {}).get("style", "2d")])
    system_prompt += f"\n\n【画幅】{frame['text']}。{frame['composition']}。"
    if grammar:
        system_prompt += f"\n\n【全书视觉语法，camera 和 light 字段必须与之一致】{grammar_text(grammar)}"
    system_prompt += (f"\n\n【本章导演意见，优先于一般偏好】{notes}" if notes else "")
    headers = {}
    from thin_review import endpoint_key  # key by variable name or key file, never on a command line
    api_key = endpoint_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    user_content = json.dumps(payload, ensure_ascii=False)
    started = time.monotonic()
    endpoints = endpoint_order(payload.get("chapter_title", "") + str(payload.get("chapter_index", ""))) if base_url == qwen_default() else [base_url]
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        if fast:
            # Fast tier keeps a short think-pass: dropping it made first drafts
            # miss coverage or come out as 30 s stubs, and the redos cost more
            # than the pass saved once planning ran in parallel.
            analysis_body = _post_any(client, endpoints, headers, {
                "model": model, "temperature": 0.3, "max_tokens": 1200,
                "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True, "reasoning_effort": "low"},
                "messages": [
                    {"role": "system", "content": system_prompt + "\n\n先做内部规划，不要输出JSON：按 episode_target 的时长分成几段、每段覆盖哪些区段和阶段数、保留哪些原文台词；每个区段至少引用一次或明确跳过。不超过400字。"},
                    {"role": "user", "content": user_content},
                ],
            })
            analysis_message = analysis_body["choices"][0]["message"]
            analysis = str(analysis_message.get("content") or analysis_message.get("reasoning") or "")[-3000:]
            analysis_seconds = round(time.monotonic() - started, 1)
        else:
          analysis_body = _post_any(client, endpoints, headers, {
              "model": model,
              "temperature": 0.3,
              "max_tokens": analysis_tokens,
              "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True, "reasoning_effort": "low"},
              "messages": [
                  {"role": "system", "content": system_prompt + "\n\n" + analysis_instruction()},
                  {"role": "user", "content": user_content},
              ],
          })
          analysis_message = analysis_body["choices"][0]["message"]
          analysis = str(analysis_message.get("content") or analysis_message.get("reasoning") or "")[-6000:]
          analysis_seconds = round(time.monotonic() - started, 1)
        body = _post_any(client, endpoints, headers, {
            "model": model,
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "thin_chapter_clips", "strict": True, "schema": schema},
            },
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
                {"role": "user", "content": "内部规划已完成。按下面的规划和原始请求直接输出符合JSON Schema的最终对象，不要解释。\n内部规划：" + analysis},
            ],
        })
    choice = body["choices"][0]
    content = choice["message"].get("content") or ""
    meta = {
        "finish_reason": choice.get("finish_reason"),
        "usage": body.get("usage"),
        "analysis_usage": analysis_body.get("usage"),
        "analysis_seconds": analysis_seconds,
        "seconds": round(time.monotonic() - started, 1),
        "analysis": analysis,
    }
    return content, meta


ALIASES: dict[str, str] = {}  # alias -> canonical character name (bible_aliases.json)
FAST_TIER = False
CHAT_SELF = ""  # the protagonist, from chat_screen.json: a private chat is named after the OTHER party
CHAT_CARD_MODE = True  # chat_screen.json render != "card" turns it off: messages are then filmed and take reading time
TEXT_ON_PROPS_GATE = True  # genre preset: readable text on props is a hard gate (古风碑文) or a note (都市招牌)


def canonical(name: str) -> str:
    return ALIASES.get(str(name).strip(), str(name).strip())


TITLE_SUFFIXES = ("公主", "殿下", "女士", "先生", "小姐", "夫人", "伯爵", "侯爵", "公爵", "男爵", "爵士", "王子", "国王", "王后",
                  "陛下", "大人", "修女", "神父", "主教", "婆婆", "船长", "医生", "教授", "老师", "队长", "警长", "侦探", "管家")


def short_forms(name: str) -> set[str]:
    """How the prose refers to a bible character besides the full name: the given name before a
    ·surname (琥珀·高德 → 琥珀), the name without its title (薇奥拉公主 → 薇奥拉), and 小 plus either
    (小琥珀).  Nothing shorter than two characters, so 船长 or 神 never gain a form.  Two characters
    sharing a form are both offered; the model picks."""
    base = str(name).strip()
    forms: set[str] = set()
    if "·" in base:
        given = base.split("·", 1)[0].strip()
        if len(given) >= 2:
            forms.add(given)
    for suffix in TITLE_SUFFIXES:
        if base.endswith(suffix) and len(base) - len(suffix) >= 2:
            forms.add(base[: -len(suffix)])
    for form in list(forms):
        if not form.startswith("小"):
            forms.add("小" + form)
    forms.discard(base)
    return forms


ENTITY_FORMS: dict[str, list[str]] = {}  # name -> forms that occur in the book (entity_index.json), when built
ENTITY_TIERS: dict[str, str] = {}


def load_entity_index(novel_dir: Path) -> bool:
    """entity_index.json (build_entity_index.py): the forms each character is actually called by in this
    book, already unique.  When it is there, name lookups use it instead of guessing."""
    path = Path(novel_dir) / "entity_index.json"
    ENTITY_FORMS.clear()
    ENTITY_TIERS.clear()
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for row in index.get("characters", []):
        forms = [f for f in (row.get("forms") or {}) if len(f) >= 2 or f == row.get("name")]
        ENTITY_FORMS[row["name"]] = sorted({row["name"], *forms}, key=len, reverse=True)
        ENTITY_TIERS[row["name"]] = str(row.get("tier") or "")
    _FORMS_INDEX.clear()
    return bool(ENTITY_FORMS)


def name_forms(name: str) -> set[str]:
    """Every string that names this character: from the entity index when the book has one, else the name,
    its aliases from bible_aliases.json and its derived short forms."""
    if name in ENTITY_FORMS:
        return set(ENTITY_FORMS[name])
    return {name, *(alias for alias, target in ALIASES.items() if target == name), *short_forms(name)}


_FORMS_INDEX: dict[tuple, dict[str, list[str]]] = {}


def _usable_forms(everyone: tuple[str, ...]) -> dict[str, list[str]]:
    """name -> the strings that point at that character and nobody else.  A form two characters share
    (约翰 for 约翰·华生 and 约翰·邓恩教授) or that sits inside another character's name (赫尔 in 赫尔曼)
    would make the prose add the wrong person, so it is dropped; the full name always stays.  Indexed
    once per cast list and alias table."""
    key = (everyone, len(ALIASES), len(ENTITY_FORMS))
    if key in _FORMS_INDEX:
        return _FORMS_INDEX[key]
    forms = {name: {f for f in name_forms(name) if len(f) >= 2} for name in everyone}
    owners: dict[str, set[str]] = {}
    for name, own in forms.items():
        for form in own:
            owners.setdefault(form, set()).add(name)
    usable: dict[str, list[str]] = {}
    for name, own in forms.items():
        keep = [form for form in own if form == name or owners[form] == {name}]
        usable[name] = sorted(keep, key=len, reverse=True)
    _FORMS_INDEX[key] = usable
    return usable


def scan_mentions(text: str, forms_by_name: dict[str, list[str]]) -> list[tuple[int, str, str]]:
    """(position, name, form) for every mention in the text, longest form first at each position and
    never overlapping: 赫尔曼 is 赫尔曼, not 赫尔男爵's 赫尔; 莱恩·诺克斯·格雷 is one mention, not three."""
    forms = sorted(((form, name) for name, own in forms_by_name.items() for form in own if form), key=lambda fn: -len(fn[0]))
    if not forms:
        return []
    pattern = re.compile("|".join(re.escape(form) for form, _ in forms))
    owner = {form: name for form, name in forms}
    return [(m.start(), owner[m.group(0)], m.group(0)) for m in pattern.finditer(text)]


def mentioned_characters(text: str, everyone: list[str]) -> list[str]:
    """Bible characters a piece of prose names, in order of first mention.  A two-character name with
    neither surname nor title (灵魂, 秘女, 船长, 天使) is a common noun as often as a person and is left
    to the model - 761's cat was the price of trusting the model alone with everyone else."""
    usable = _usable_forms(tuple(everyone))
    eligible = {name: usable.get(name, []) for name in everyone if len(name) >= 3 or "·" in name}
    seen: list[str] = []
    for _, name, _ in scan_mentions(text, eligible):
        if name not in seen:
            seen.append(name)
    return seen


def complete_characters(characters: list[str], shot: dict, everyone: list[str], cap: int = 6) -> tuple[list[str], list[str]]:
    """The shot's cast plus every character its own description puts on camera.  2026-09-13, 雾月 761:
    the description said 薇奥拉 kissed 莱恩, the enum had no 薇奥拉, the cast was [莱恩, 琥珀] - and the
    cat did the kissing.  Returns the cast and what was added."""
    described = mentioned_characters(f"{shot.get('visual_prompt') or ''}\n{shot.get('motion_prompt') or ''}", everyone)
    added = [name for name in described if name not in characters][: max(0, cap - len(characters))]
    return list(characters) + added, added


def validate_and_normalize(raw: dict, segments: list[dict], bible: StoryBible, location_map: dict[str, str], chapter_text: str,
                           everyone: list[str] | None = None) -> tuple[list[str], list[str], list[dict]]:
    errors: list[str] = []
    warnings: list[str] = []
    names = [character.name for character in bible.characters]
    everyone = list(everyone) if everyone else names  # the whole bible: a description may name someone the slice left out
    segment_keys = {segment["segment_id"]: quote_key(segment["text"]) for segment in segments}
    chapter_key = quote_key(chapter_text)
    shots = flatten_clips(raw)
    if not shots:
        return ["no clips/stages returned"], warnings, []
    clip_count = len(raw.get("clips") or [])
    for clip in raw.get("clips") or []:
        if isinstance(clip, dict) and len(clip.get("stages") or []) > STAGE_RANGE[1]:
            warnings.append(f"{clip.get('clip_id')}: {len(clip['stages'])} stages; packer will split")
    if not CLIP_RANGE[0] <= clip_count <= CLIP_RANGE[1]:
        warnings.append(f"clip_count {clip_count} outside {CLIP_RANGE[0]}-{CLIP_RANGE[1]} (report only)")
    cited: dict[str, list[int]] = {}
    normalized: list[dict] = []
    for position, shot in enumerate(shots, start=1):
        position = shot.get("label") or f"shot {position}"
        segment_id = str(shot.get("segment_id", ""))
        quote = str(shot.get("source_quote", "")).strip()
        key = quote_key(quote)
        if len(key) > QUOTE_MAX_CHARS:
            # Seventeen of the trial's redo errors were quotes over the cap.  The
            # words are verbatim; only the length is wrong, so keep the head up
            # to a sentence end rather than sending the chapter back for a redo.
            quote = trim_quote(quote)
            warnings.append(f"{position}: source_quote {len(key)} chars, cut at a sentence end to {len(quote_key(quote))}")
            key = quote_key(quote)
        full_line = key in {quote_key(q) for q in chapter_quotes(chapter_text)}
        # Length is judged on what the model wrote (punctuation included), the
        # same count it was given in the schema; the key is only for matching.
        if len(re.sub(r"[\s\u3000]+", "", quote)) < QUOTE_MIN_CHARS and not full_line:
            errors.append(f"{position}: source_quote too short, need at least {QUOTE_MIN_CHARS} chars: {quote!r}")
        else:
            found = [sid for sid, segment_key in segment_keys.items() if key in segment_key]
            if segment_id in found:
                pass
            elif found:
                warnings.append(f"{position}: source_quote belongs to {found[0]}, not {segment_id}; reassigned")
                segment_id = found[0]
            elif key in chapter_key:
                warnings.append(f"{position}: source_quote spans a segment boundary; kept {segment_id}")
            else:
                # A quote that stitches two lines of the same exchange together
                # (the narration between them dropped) is still provenance: every
                # word is verbatim, so accept it and note which segment it lands in.
                pieces = [quote_key(piece) for piece in re.split(r"[\n\r]+", quote) if len(quote_key(piece)) >= 4]
                if pieces and all(piece in chapter_key for piece in pieces):
                    owners = [sid for sid, segment_key in segment_keys.items() if pieces[0] in segment_key]
                    if owners and segment_id not in owners:
                        warnings.append(f"{position}: source_quote 由 {len(pieces)} 行原文拼成，归到 {owners[0]}")
                        segment_id = owners[0]
                    else:
                        warnings.append(f"{position}: source_quote 由 {len(pieces)} 行原文拼成（中间的叙述被略去）")
                else:
                    nearest = closest_source_line(quote, chapter_text)
                    errors.append(
                        f"{position}: source_quote 不是原文（疑似改写）：{quote[:50]!r}。"
                        + (f"最接近的原文句子是：{nearest!r}，请逐字复制这一句或它所在段落里的一段连续原文" if nearest else "请从对应区段逐字复制一段连续原文")
                    )
        cited.setdefault(segment_id, []).append(position)

        characters = list(dict.fromkeys(canonical(name) for name in shot.get("characters", []) if canonical(name) in names))
        unknown = [str(name) for name in shot.get("characters", []) if canonical(name) not in names]
        if unknown:
            errors.append(f"{position}: characters not in StoryBible: {unknown}")
        characters, added = complete_characters(characters, shot, everyone)
        if added:
            warnings.append(f"{position}: characters 补上镜头描述里出现的 {added}")
        location = str(shot.get("location", ""))
        if location not in location_map:
            errors.append(f"{position}: unknown location {location!r}; allowed: {list(location_map)}")

        turns_out: list[dict] = []
        visible: list[str] = []
        for turn in shot.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            mode = str(turn.get("delivery_mode", ""))
            speaker = canonical(turn.get("speaker_name", ""))
            text = str(turn.get("text", "")).strip()
            emotion = str(turn.get("emotion", "")).strip() or "克制自然"
            if not text:
                continue
            if mode == "visible_dialogue":
                if speaker in names:
                    if speaker not in characters:
                        characters.append(speaker)
                        warnings.append(f"{position}: visible speaker {speaker} added to characters")
                    visible.append(speaker)
                elif speaker.startswith("无名") or speaker in ANONYMOUS_SPEAKERS:
                    warnings.append(f"{position}: anonymous {speaker} cannot be visible; converted to offscreen")
                    mode = "offscreen_dialogue"
                else:
                    errors.append(f"{position}: visible speaker {speaker!r} is not a StoryBible character")
            elif mode == "offscreen_dialogue":
                if not speaker:
                    errors.append(f"{position}: offscreen_dialogue needs speaker_name")
                elif speaker not in names and not speaker.startswith("无名") and speaker not in ANONYMOUS_SPEAKERS:
                    errors.append(f"{position}: offscreen speaker {speaker!r} unknown; use a StoryBible name or 无名 role")
            elif mode in {"silent_action", "title_card"}:
                speaker = ""
            elif mode == "singing":
                if speaker in names and speaker not in characters:
                    characters.append(speaker)
                if speaker not in names:
                    errors.append(f"{position}: singing 的 speaker_name 必须是 StoryBible 角色")
                if len(compact(text)) > 12 and not re.search(r"哼|唱|旋律|曲调|歌声|声音|嗓|吟", text):  # a manner description names the singing; lyrics do not
                    errors.append(f"{position}: singing 的 text 疑似歌词：{text[:20]!r}，只写演唱方式（如“轻声哼唱一段温柔的无词旋律”），不得写歌词")
            elif mode == "chat_message":
                if not speaker:
                    errors.append(f"{position}: chat_message needs speaker_name（发消息的人）")
                target = str(turn.get("chat_target") or "").strip()
                if target and target not in names:
                    warnings.append(f"{position}: chat_target {target!r} 不在 StoryBible，按群聊处理")
                    turn["chat_target"] = ""
                elif target and CHAT_SELF and speaker == CHAT_SELF and target != CHAT_SELF:
                    pass  # the protagonist writing into a private chat: normal
                elif target and CHAT_SELF and target == CHAT_SELF:
                    # A private chat is titled with the other party, never with the protagonist.
                    warnings.append(f"{position}: chat_target 写成了主角 {target!r}，按群聊处理")
                    turn["chat_target"] = ""

                if len(compact(text)) > CHAT_MAX_CHARS:
                    # A long message is cut, not rejected: the bubble just shows its first clause.
                    cut = text[:CHAT_MAX_CHARS]
                    boundary = max(cut.rfind(mark) for mark in "，。！？；：、,.!?;:")
                    if boundary >= CHAT_MAX_CHARS // 2:
                        cut = cut[:boundary + 1]
                    cut = cut.rstrip("，,、；;：:") + "…"
                    warnings.append(f"{position}: chat_message {len(compact(text))} 字，截为 {cut!r}")
                    text = cut
            else:
                errors.append(f"{position}: unknown delivery_mode {mode!r}")
                continue
            pieces = split_turn_text(text) if mode in {"visible_dialogue", "offscreen_dialogue"} else [text]
            if len(pieces) > 1:
                warnings.append(f"{position}: turn of {spoken_chars(text)} chars split into {len(pieces)}")
            for piece in pieces:
                turns_out.append({"speaker_name": speaker, "delivery_mode": mode, "chat_target": str(turn.get("chat_target") or "").strip(), "text": piece, "emotion": emotion})
        if not turns_out:
            fallback = str(shot.get("motion_prompt") or shot.get("end_state") or "无声反应").strip()
            turns_out = [{"speaker_name": "", "delivery_mode": "silent_action", "text": fallback[:60], "emotion": "克制自然"}]
            warnings.append(f"{position}: no usable turns; added silent_action")

        end_state = str(shot.get("end_state") or "").strip()
        if not end_state:
            end_state = str(shot.get("motion_prompt") or "").strip()[:80]
            warnings.append(f"{position}: end_state missing; derived from motion_prompt")
        has_chat = any(t.get("delivery_mode") == "chat_message" for t in turns_out)
        for field in ("visual_prompt", "motion_prompt", "end_state", "camera", "light"):
            value = str(shot.get(field) or "")
            for pattern, label in FORBIDDEN_VISUAL:
                if label == "可读文字" and has_chat:
                    continue  # the phone screen is supposed to show the messages
                match = pattern.search(value)
                if match:
                    message = (f"{position}: {field} 含{label}描述（{match.group(0)}），图片和视频都不允许；"
                               "去掉血迹和伤口，碑上的结果改写为无字的发光纹路")
                    if label == "可读文字" and (FAST_TIER or not TEXT_ON_PROPS_GATE):
                        warnings.append("report only: " + message)  # fast tier or genre policy: a note, not a gate
                    else:
                        errors.append(message)
        base = {
            "clip_hint": shot.get("clip_hint"),
            "segment_id": segment_id,
            "source_quote": quote,
            "location": location,
            "characters": characters,
            "visual_prompt": str(shot.get("visual_prompt") or "").strip(),
            "motion_prompt": str(shot.get("motion_prompt") or "").strip(),
            "end_state": end_state,
            "camera": str(shot.get("camera") or "").strip(),
            "light": str(shot.get("light") or "").strip(),
            "avoid": str(shot.get("avoid") or "").strip(),
            "sfx": str(shot.get("sfx") or "").strip(),
            "shot_scale": str(shot.get("shot_scale") or "中近景"),
            "origin_index": len(normalized) + 1,
        }
        distinct_visible = list(dict.fromkeys(visible))
        if len(distinct_visible) <= 1:
            normalized.append({**base, "turns": turns_out})
            continue
        warnings.append(f"{position}: {len(distinct_visible)} visible speakers; split into consecutive shots")
        groups: list[list[dict]] = []
        current_speaker = None
        for turn in turns_out:
            if turn["delivery_mode"] == "visible_dialogue" and turn["speaker_name"] != current_speaker:
                if groups and current_speaker is None:
                    groups[-1].append(turn)
                    current_speaker = turn["speaker_name"]
                    continue
                groups.append([turn])
                current_speaker = turn["speaker_name"]
                continue
            if not groups:
                groups.append([])
            groups[-1].append(turn)
        for group in groups:
            if group:
                normalized.append({**base, "turns": group})

    clip_seconds: dict[str, float] = {}
    for shot in normalized:
        clip_seconds[shot["clip_hint"]] = round(clip_seconds.get(shot["clip_hint"], 0.0) + stage_seconds(shot["turns"]), 2)
    for clip_id, seconds in clip_seconds.items():
        if seconds > MAX_CLIP_SECONDS + CLIP_SECONDS_TOLERANCE:
            # The packer already cuts a clip that exceeds the model's 30 s
            # ceiling, so this is a note about extra cuts, not a defect.
            warnings.append(
                f"{clip_id}: 估算 {seconds} 秒超过单段上限 {int(MAX_CLIP_SECONDS)} 秒，打包时会自动拆成两段（report only）"
            )
    total_seconds = round(sum(clip_seconds.values()), 2)
    if EPISODE_SECONDS_MIN and total_seconds < EPISODE_SECONDS_MIN:
        errors.append(
            f"全集估算只有 {total_seconds} 秒，低于本次要求的下限 {int(EPISODE_SECONDS_MIN)} 秒（目标约90秒）；"
            "把当前章还没拍到的事件补成阶段，把叙述里的来历、规则和动机多外化成角色对白或画外议论，"
            "或给已有阶段增加有原文依据的问答，不得注水重复同一句意思"
        )
    if total_seconds > EPISODE_SECONDS_MAX and FAST_TIER:
        warnings.append(f"report only: 全集估算 {total_seconds} 秒，快速档不返修，打包时按 30 秒拆段")
    elif total_seconds > EPISODE_SECONDS_MAX:
        stage_total = len(normalized)
        spoken_total = sum(spoken_chars(t["text"]) for s in normalized for t in s["turns"] if t["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"})
        scale = 92.0 / total_seconds
        stage_target = max(10, round(stage_total * scale))
        errors.append(
            f"全集估算 {total_seconds} 秒，超过上限 {int(EPISODE_SECONDS_MAX)} 秒（目标约90秒）。"
            f"上一稿是 {stage_total} 个阶段、发声 {spoken_total} 字；本次压到 {stage_target} 个阶段左右、"
            f"发声 {max(150, round(spoken_total * scale))} 字左右。做法是缩短台词：合并同一人的连续短句，删掉不带新信息的群众议论和感叹，"
            "去掉只有反应没有事件的无声阶段；每个阶段最多两句短台词。不得为了缩短而删掉整个区段：每个区段仍须至少被一个阶段引用，一个都不能少。不得原样重发上一稿。"
        )
    skipped_raw = raw.get("skipped_segments") or []
    skipped = {str(item.get("segment_id")): str(item.get("reason", "")) for item in skipped_raw if isinstance(item, dict)}
    for segment_id in list(skipped):
        if segment_id in cited:
            warnings.append(f"{segment_id} listed as skipped but also cited; skip ignored")
            skipped.pop(segment_id)
    if len(skipped) > MAX_SKIPPED:
        errors.append(f"不允许跳过区段，skipped_segments 必须为空，但收到 {sorted(skipped)}：把这些区段各写进至少一个阶段（可以拉长集数）")
    chat_speakers = re.findall(r"^([^\n：:]{2,8})[：:]", chapter_text, re.M)
    chat_source_lines = len(chat_speakers)
    # A chat has somebody speaking more than once; a stat block (法宝名称：…
    # 法宝属性：… 法宝等级：…) has the same line shape but every label once.
    looks_like_chat = chat_source_lines >= 5 and max((chat_speakers.count(s) for s in set(chat_speakers)), default=0) >= 2
    if looks_like_chat and not any(turn["delivery_mode"] == "chat_message" for shot in normalized for turn in shot["turns"]):
        errors.append(
            f"本章原文有 {chat_source_lines} 行聊天消息（形如「昵称：内容」），但没有任何 chat_message："
            "群聊和私聊必须用 chat_message 呈现（一个阶段最多八条），不得改写成画外音或角色自述"
        )
    uncited = [s["segment_id"] for s in segments if s["segment_id"] not in cited and s["segment_id"] not in skipped]
    for segment_id in uncited:
        segment = next(s for s in segments if s["segment_id"] == segment_id)
        errors.append(
            f"{segment_id} is neither cited by any shot nor listed in skipped_segments; "
            f"it begins with: {segment['text'][:40]!r}"
        )
    return errors, warnings, normalized


def to_episode_plan(raw: dict, shots: list[dict], location_map: dict[str, str], chapter_text: str, chapter_title: str) -> EpisodePlan:
    chapter_key = quote_key(chapter_text)
    plan_shots: list[Shot] = []
    for index, shot in enumerate(shots, start=1):
        turns: list[ScriptTurn] = []
        for turn in shot["turns"]:
            mode = turn["delivery_mode"]
            text = turn["text"]
            common = {
                "text": text,
                "emotion": turn.get("emotion") or "克制自然",
                "source_quote": shot["source_quote"][:500],
            }
            if mode == "silent_action":
                turns.append(ScriptTurn(role="action", speaker_name="", speaking=False, delivery_mode=TurnDelivery.SILENT_ACTION, derivation=TurnDerivation.DERIVED, **common))
            elif mode == "singing":
                turns.append(ScriptTurn(role="action", speaker_name="", speaking=False, delivery_mode=TurnDelivery.SILENT_ACTION, derivation=TurnDerivation.DERIVED, **{**common, "text": f"{turn['speaker_name']}哼唱：{text}"}))
            elif mode == "chat_message":  # the legacy plan model has no chat kind; keep it as a silent on-screen action
                turns.append(ScriptTurn(role="action", speaker_name="", speaking=False, delivery_mode=TurnDelivery.SILENT_ACTION, derivation=TurnDerivation.DERIVED, **{**common, "text": f"屏幕消息 {turn['speaker_name']}：{text}"}))
            elif mode == "title_card":
                turns.append(ScriptTurn(role="narrator", speaker_name="旁白", speaking=False, delivery_mode=TurnDelivery.TITLE_CARD, derivation=TurnDerivation.DERIVED, **common))
            else:
                derivation = TurnDerivation.VERBATIM if quote_key(text) in chapter_key else TurnDerivation.DERIVED
                turns.append(
                    ScriptTurn(
                        role=turn["speaker_name"],
                        speaker_name=turn["speaker_name"],
                        speaking=(mode == "visible_dialogue"),
                        delivery_mode=TurnDelivery(mode),
                        derivation=derivation,
                        **common,
                    )
                )
        first_text = turns[0].text if turns else ""
        narration = (shot.get("end_state") or first_text or "推进")[:80]
        plan_shots.append(
            Shot(
                index=index,
                narration=narration or "推进",
                subtitle=(first_text or narration)[:80] or "……",
                visual_prompt=shot["visual_prompt"] or narration,
                motion_prompt=shot["motion_prompt"] or narration,
                characters=shot["characters"],
                location=location_map[shot["location"]],
                source_quote=shot["source_quote"][:500],
                scene_job="推进",
                change=(shot.get("end_state") or "")[:240],
                shot_scale=shot["shot_scale"],
                turns=turns,
            )
        )
    return EpisodePlan(
        video_title=str(raw.get("video_title") or chapter_title),
        hook=str(raw.get("hook") or ""),
        summary=str(raw.get("summary") or ""),
        shots=plan_shots,
        creative_profile=POLICY,
    )


def metrics(shots: list[dict], chapter_text: str, segments: list[dict], skipped: dict) -> dict:
    chapter_key = quote_key(chapter_text)
    spoken = 0
    verbatim = 0
    turn_count = 0
    by_mode: dict[str, int] = {}
    speakers: dict[str, int] = {}
    for shot in shots:
        for turn in shot["turns"]:
            turn_count += 1
            by_mode[turn["delivery_mode"]] = by_mode.get(turn["delivery_mode"], 0) + 1
            if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"}:
                chars = spoken_chars(turn["text"])
                spoken += chars
                if quote_key(turn["text"]) in chapter_key:
                    verbatim += chars
                speakers[turn["speaker_name"]] = speakers.get(turn["speaker_name"], 0) + 1
    coverage = {
        segment["segment_id"]: [shot["origin_index"] for shot in shots if shot["segment_id"] == segment["segment_id"]]
        for segment in segments
    }
    return {
        "shot_count": len(shots),
        "missing_quoted_lines": missing_quotes(shots, chapter_text),
        "turn_count": turn_count,
        "turns_by_mode": by_mode,
        "spoken_chars": spoken,
        "verbatim_spoken_chars": verbatim,
        "verbatim_ratio": round(verbatim / spoken, 3) if spoken else None,
        "speakers": speakers,
        "segment_coverage": coverage,
        "skipped_segments": skipped,
    }


def missing_quotes(shots: list[dict], chapter_text: str) -> list[str]:
    joined = quote_key("".join(turn["text"] for shot in shots for turn in shot["turns"]))
    return [quote for quote in chapter_quotes(chapter_text) if quote_key(quote) not in joined]


def strict_plan_errors(shots: list[dict], chapter_text: str, segments: list[dict], raw: dict) -> list[str]:
    """Opt-in gates (NOVEL_PLAN_STRICT=1), kept to what changes an episode's
    length a lot: a plan far below or far above the spoken budget is redone
    once or twice.  Wording fidelity, the closing line and the share of quoted
    lines stay report-only (the user chose throughput over verbatim lines)."""
    skipped = {str(item.get("segment_id")): str(item.get("reason", "")) for item in (raw.get("skipped_segments") or []) if isinstance(item, dict)}
    found = metrics(shots, chapter_text, segments, skipped)
    missing = list(found.get("missing_quoted_lines", []))
    low, high = SPOKEN_RANGE
    errors: list[str] = []
    spoken_turns = [turn["text"] for shot in shots for turn in shot["turns"] if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"}]
    if found["spoken_chars"] < low * 0.6:
        errors.append(f"发声字数 {found['spoken_chars']} 远低于下限 {low}：把下列原文台词加回对应阶段，作为可见或画外台词（可适当精简）：" + " / ".join(q[:40] for q in missing[:8]))
    elif found["spoken_chars"] > high * 1.5:
        longest = "；".join(f"「{text[:24]}…」({spoken_chars(text)}字)" for text in sorted(spoken_turns, key=spoken_chars, reverse=True)[:5])
        errors.append(f"发声字数 {found['spoken_chars']} 远高于上限 {high}，至少删掉 {found['spoken_chars'] - high} 字：删除或精简寒暄、铺垫和重复的台词（例如 {longest}），"
                      "或把整句改为一句动作描述；不要新增台词")
    return errors


def soft_warnings(report_metrics: dict) -> list[str]:
    notes = []
    for quote in report_metrics.get("missing_quoted_lines", []):
        notes.append(f"原文引号台词未出现或被删改（report only）：{quote[:40]}")
    low, high = SHOT_RANGE
    if not low <= report_metrics["shot_count"] <= high:
        notes.append(f"shot_count {report_metrics['shot_count']} outside {low}-{high} (report only)")
    if not SPOKEN_RANGE[0] <= report_metrics["spoken_chars"] <= SPOKEN_RANGE[1]:
        notes.append(f"spoken_chars {report_metrics['spoken_chars']} outside {SPOKEN_RANGE[0]}-{SPOKEN_RANGE[1]} (report only)")
    return notes


MODE_LABEL = {
    "visible_dialogue": "可见",
    "offscreen_dialogue": "画外",
    "silent_action": "动作",
    "title_card": "字幕卡",
    "chat_message": "群消息",
    "singing": "哼唱",
}


def render_markdown(raw: dict, shots: list[dict], report: dict, chapter_title: str) -> str:
    lines = [
        f"# {raw.get('video_title') or chapter_title}",
        "",
        f"钩子：{raw.get('hook', '')}",
        "",
        f"梗概：{raw.get('summary', '')}",
        "",
        f"镜数 {report['metrics']['shot_count']} · turn {report['metrics']['turn_count']} · 发声字数 {report['metrics']['spoken_chars']} · 逐字率 {report['metrics']['verbatim_ratio']}",
        "",
    ]
    for index, shot in enumerate(shots, start=1):
        cast = "、".join(shot["characters"]) or "无人物"
        lines.append(f"## 镜{index} · {shot.get('clip_hint') or ''} · {shot['location']} · {shot['shot_scale']} · {cast}")
        lines.append(f"开始时：{shot['visual_prompt']}")
        lines.append(f"主要事件：{shot['motion_prompt']}")
        lines.append(f"结束时：{shot['end_state']}")
        if shot.get("camera"):
            lines.append(f"机位：{shot['camera']}")
        if shot.get("light"):
            lines.append(f"光源：{shot['light']}")
        if shot.get("avoid"):
            lines.append(f"本段不要：{shot['avoid']}")
        if shot.get("sfx"):
            lines.append(f"音效：{shot['sfx']}")
        for turn in shot["turns"]:
            label = MODE_LABEL.get(turn["delivery_mode"], turn["delivery_mode"])
            who = turn["speaker_name"] or label
            prefix = f"【{who}·{label}】" if turn["speaker_name"] else f"【{label}】"
            lines.append(f"- {prefix}{turn['text']}")
        lines.append(f"原文（{shot['segment_id']}）：{shot['source_quote']}")
        lines.append("")
    if report.get("warnings"):
        lines.append("## 自动修正与提示")
        lines.extend(f"- {item}" for item in report["warnings"])
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--title")
    parser.add_argument("--episode-index", type=int, default=1)
    parser.add_argument("--bible", required=True, help="existing story_bible.json to reuse")
    parser.add_argument("--output-root", default="outputs")
    parser.add_argument("--base-url", default=qwen_default(), help="one Qwen endpoint; the default spreads over every QWEN38_LOCAL_BASE_URL entry")
    parser.add_argument("--model", default=os.getenv("QWEN38_LOCAL_MODEL", "Qwen3.8-27B-Project"))
    # A healthy clip plan is 4.5-6.5K tokens.  Constrained decoding can derail
    # into endless whitespace; a tight cap turns that into a fast, cheap redo.
    parser.add_argument("--max-tokens", type=int, default=int(os.environ.get("NOVEL_PLAN_MAX_TOKENS") or (12000 if SHORT_CLIPS else 9000)),
                        help="completion budget; the 15 s mode writes 6-8 clips and needs more room (chapter 381 was cut off three times at 9000)")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--max-redo", type=int, default=1)
    parser.add_argument("--min-seconds", type=float, default=0.0, help="reject a plan shorter than this (drives the redo)")
    parser.add_argument("--notes", default="", help="director feedback injected into this chapter's request")
    parser.add_argument("--grammar", type=Path, help="visual_grammar.json; defaults to <output-root>/<novel-id>/visual_grammar.json when present")
    parser.add_argument("--style", choices=("2d", "3d"), help="override profile.json style")
    parser.add_argument("--frame", choices=("9:16", "16:9"), help="override profile.json frame")
    parser.add_argument("--dry-run", action="store_true", help="build segments and request only")
    parser.add_argument("--replay", type=Path, help="validate an existing raw response instead of calling the model")
    parser.add_argument("--merge", type=int, default=1, help="chapters per episode: episode k covers chapters (k-1)*N+1..k*N")
    parser.add_argument("--tier", choices=("quality", "fast"), help="override profile.json tier")
    args = parser.parse_args()

    global EPISODE_SECONDS_MIN, MAX_SKIPPED
    EPISODE_SECONDS_MIN = args.min_seconds
    novel = read_novel(args.source, novel_id=args.novel_id, title=args.title)
    merge = max(1, args.merge)
    episode_count = -(-len(novel.episodes) // merge)
    if not 1 <= args.episode_index <= episode_count:
        raise SystemExit(f"episode index out of range 1..{episode_count} (merge {merge})")
    if merge == 1:
        episode = novel.episodes[args.episode_index - 1]
    else:
        # Thin web-novel chapters: one episode covers N consecutive chapters.
        group = novel.episodes[(args.episode_index - 1) * merge: args.episode_index * merge]
        joined = "\n\n".join(f"{e.source_title}\n{e.source_text}" for e in group)
        episode = group[0].model_copy(update={
            "index": args.episode_index,
            "source_title": f"{group[0].source_title} 至 {group[-1].source_title.split(' ', 1)[0]}" if len(group) > 1 else group[0].source_title,
            "source_text": joined, "text_count": sum(e.text_count for e in group),
            "source_start": group[0].source_start, "source_end": group[-1].source_end,
        })
    full_bible = StoryBible.model_validate_json(Path(args.bible).read_text(encoding="utf-8"))
    # Per-chapter slice: a long novel's bible has hundreds of entries, but the
    # prompt and the JSON enums only need the main cast plus whoever and
    # wherever this chapter mentions.  Asset ids come from positions in the
    # full bible (the packer maps names back), so slicing costs nothing.
    chapter_text = episode.source_text
    novel_dir = Path(args.output_root).resolve() / args.novel_id
    episode_dir = novel_dir / f"{args.novel_id}_{episode.index}"
    profile = load_profile(novel_dir, style=args.style, frame=args.frame, tier=args.tier)
    genre = load_genre(profile)
    global ANONYMOUS_SPEAKERS, TEXT_ON_PROPS_GATE, SYSTEM_PROMPT
    ANONYMOUS_SPEAKERS = list(genre.get("anonymous_roles") or ANONYMOUS_SPEAKERS)
    # The brief's worked examples (light sources, avoid lines, text on props,
    # anonymous roles) come from the genre file; the xianxia wording in the
    # brief itself is only the default they replace.
    for key, default in PROMPT_EXAMPLE_DEFAULTS.items():
        SYSTEM_PROMPT = SYSTEM_PROMPT.replace(default, str((genre.get("prompt_examples") or {}).get(key) or default))
    TEXT_ON_PROPS_GATE = genre.get("text_on_props", "report") == "gate"
    fast = is_fast(profile)
    global FAST_TIER
    FAST_TIER = fast
    fast_target = 60
    if fast:
        # Fast tier: no think-pass, length overruns are warnings (the packer
        # splits anyway), up to two missing segments are auto-skipped.  The
        # episode target scales with the text (~60 s per 3000 chars, max 100 s)
        # so merged chapters do not come out as 40 s stubs.  Redos stay at two:
        # with parallel planning they are cheap and lift the pass rate.
        global CLIP_RANGE, STAGE_RANGE, SPOKEN_RANGE, EPISODE_SECONDS_MAX
        fast_target = int(min(150, max(75, round(episode.text_count / 3000 * 85 / 10) * 10)))
        # The brief is rendered from these, so they must follow the lane: the 15 s lane
        # keeps twice the clips at half the stages, exactly as its brief has always said.
        CLIP_RANGE = (6, 8) if SHORT_CLIPS else (3, 5)  # every segment gets filmed, so the count follows the chapter
        STAGE_RANGE = (2, 3) if SHORT_CLIPS else (3, 5)  # the fast tier's requirements line has always said 3-5
        SPOKEN_RANGE = (180, 300) if fast_target <= 90 else (240, 400)
        EPISODE_SECONDS_MAX = 210.0
        EPISODE_SECONDS_MIN = max(EPISODE_SECONDS_MIN, fast_target - 25)  # soft: waived on the last redo
    chat_screen_path = novel_dir / "chat_screen.json"
    if chat_screen_path.is_file():
        global CHAT_SELF
        CHAT_SELF = str(json.loads(chat_screen_path.read_text(encoding="utf-8")).get("self_name", "")).strip()
        global CHAT_CARD_MODE
        CHAT_CARD_MODE = str(json.loads(chat_screen_path.read_text(encoding="utf-8")).get("render", "card")) == "card"
    aliases_path = novel_dir / "bible_aliases.json"
    ALIASES.update(json.loads(aliases_path.read_text(encoding="utf-8")) if aliases_path.is_file() else {})
    load_entity_index(novel_dir)

    # Which characters and locations the planner may name.  The whole bible is
    # never sent: at a few thousand chapters it would be hundreds of people the
    # model has no use for.  A character is offered when they are a lead, when
    # this chapter names them (by name OR by any alias, which the old slice
    # missed - an unoffered character comes back as a duplicate entry), or when
    # they were on screen in the last few chapters, which keeps a scene that
    # refers to someone as "he" from losing them.
    cast = cast_history(novel_dir)
    aliases_of = {}
    for alias, target in ALIASES.items():
        aliases_of.setdefault(target, []).append(alias)

    def named_here(name: str) -> bool:
        return bool(name) and any(form in chapter_text for form in name_forms(name))

    recent_characters = recent_names(cast.get("characters", {}), episode.index, CAST_RECENT_CHAPTERS)
    recent_locations = recent_names(cast.get("locations", {}), episode.index, CAST_RECENT_CHAPTERS)
    main_cast = [c for c in full_bible.characters if "主角" in c.role]
    present = [c for c in full_bible.characters if named_here(c.name)]
    carried = [c for c in full_bible.characters if c.name in recent_characters]
    sliced_characters = list({c.name: c for c in [*main_cast, *present, *carried]}.values()) or full_bible.characters[:8]
    # A location is known from the chapter that added it (bible_growth.json);
    # the base bible's locations count as known from the start.  Never offer a
    # place the story has not reached, and when nothing is named, fall back to
    # the most recently introduced places BEFORE this chapter, not the newest
    # ones in a bible that may be a thousand chapters ahead.
    added_at: dict[str, int] = {}
    growth_path = novel_dir / "bible_growth.json"
    if growth_path.is_file():
        try:
            for ch, entry in json.loads(growth_path.read_text(encoding="utf-8")).items():
                for loc in entry.get("locations", []) or []:
                    added_at.setdefault(str(loc).split("：", 1)[0].strip(), int(ch))
        except (OSError, ValueError):
            added_at = {}

    def short_of(full: str) -> str:
        return full.split("：", 1)[0].strip()

    known = [full for full in full_bible.locations if added_at.get(short_of(full), 0) <= episode.index]
    sliced_locations = [full for full in known
                        if named_here(short_of(full)) or short_of(full) in recent_locations
                        or episode.index - CAST_RECENT_CHAPTERS <= added_at.get(short_of(full), -1) <= episode.index]
    if not sliced_locations:
        sliced_locations = sorted(known, key=lambda full: -added_at.get(short_of(full), 0))[:6] or full_bible.locations[:6]
    bible = full_bible.model_copy(update={"characters": sliced_characters, "locations": sliced_locations})
    location_map = {full.split("：", 1)[0].strip(): full for full in bible.locations}
    names = [character.name for character in bible.characters]
    everyone = [character.name for character in full_bible.characters]
    grammar_path = args.grammar or (novel_dir / "visual_grammar.json")
    grammar = json.loads(grammar_path.read_text(encoding="utf-8")) if grammar_path.is_file() else None
    episode_dir.mkdir(parents=True, exist_ok=True)
    bible_target = novel_dir / "story_bible.json"
    if not bible_target.is_file():
        shutil.copy2(args.bible, bible_target)

    globals()["SEPARATE_PAIRS"] = load_separate_pairs(novel_dir)
    if SEPARATE_PAIRS:
        print(f"keeping apart: {'、'.join(f'{a}+{b}' for a, b in SEPARATE_PAIRS)}", file=sys.stderr)
    segments = split_segments(episode.source_text, episode.source_title, SEGMENT_COUNT)
    atomic_write_json(episode_dir / "segments.json", segments)
    # Rolling recap: the summaries the planner itself wrote for the previous
    # chapters, for continuity only (who is where, what just happened).
    recap_path = novel_dir / "recap.json"
    recap = json.loads(recap_path.read_text(encoding="utf-8")) if recap_path.is_file() else []
    previous_recap = [row for row in recap if int(row.get("chapter", 0)) < episode.index][-5:]
    # Recap text carries the story; the closing picture of the previous episode
    # carries the *scene* - where everyone stood and what they were doing when
    # the last clip ended - so this episode can open by picking it up rather than
    # re-establishing everything.
    previous_ending = None
    previous_script = novel_dir / f"{args.novel_id}_{episode.index - 1}" / "chapter_script.json"
    if previous_script.is_file():
        try:
            last_shot = (json.loads(previous_script.read_text(encoding="utf-8")).get("shots") or [])[-1]
            previous_ending = {"location": last_shot.get("location"), "characters": last_shot.get("characters"), "end_state": last_shot.get("end_state")}
        except (OSError, ValueError, IndexError, AttributeError):
            previous_ending = None
    for row in previous_recap:  # the planner needs the gist, not the whole paragraph
        if len(str(row.get("summary", ""))) > 180:
            row["summary"] = str(row["summary"])[:180] + "…"
    # Chapter recaps only reach five chapters back; the volume summaries carry
    # the arc so a thousand-chapter story does not drift.
    volumes_path = novel_dir / "volumes.json"
    volumes = json.loads(volumes_path.read_text(encoding="utf-8")) if volumes_path.is_file() else []
    previous_volumes = [row for row in volumes if int(row.get("to", 0)) < episode.index][-2:]
    schema = build_schema(names, list(location_map), [segment["segment_id"] for segment in segments])
    payload = {
        "policy": POLICY,
        "chapter_index": episode.index,
        "chapter_title": episode.source_title,
        "chapter_chars": episode.text_count,
        "story_bible": compact_bible(bible, location_map),
        **({"visual_grammar": grammar} if grammar else {}),
        "production_profile": profile,
        "available_characters": names,
        **({"name_aliases": {alias: target for alias, target in ALIASES.items() if target in names}, "alias_rule": "name_aliases 里的名字是同一人物的别称、昵称或网名；characters 和 speaker_name 一律写正名"} if any(target in names for target in ALIASES.values()) else {}),
        "available_locations": list(location_map),
        "anonymous_offscreen_speakers": ANONYMOUS_SPEAKERS,
        "era_setting": {"genre": genre["name"], "allowed": genre.get("era_allowed", ""), "not_allowed": genre.get("era_rejects", ""), "crowd": genre.get("crowd_default", "")},
        **({"previous_volumes_recap": previous_volumes} if previous_volumes else {}),
        **({"previous_chapters_recap": previous_recap} if previous_recap else {}),
        **({"previous_episode_ending": previous_ending, "previous_episode_ending_usage": "这是上一集最后一个画面的状态（地点、在场的人、结束时的动作）。本集开场如果是同一场景可以直接接上，不必重新交代；换了场景就忽略。"} if previous_ending else {}),
        "segments": [{"segment_id": s["segment_id"], "text": s["text"]} for s in segments],
        "quoted_lines_that_must_be_kept": chapter_quotes(episode.source_text),
        "requirements": {
            "clip_count": f"{CLIP_RANGE[0]}-{CLIP_RANGE[1]}",
            **({"episode_target": f"约{fast_target}秒，{CLIP_RANGE[0]}到{CLIP_RANGE[1]}段，每段{STAGE_RANGE[0]}到{STAGE_RANGE[1]}个阶段，全集阶段总数12到18个；{SEGMENT_COUNT}个区段每一个都必须至少被一个阶段引用（每个区段1到{STAGES_PER_SEGMENT_MAX}个阶段），skipped_segments 必须为空——长对话压成一两句、群众议论合并、次要过程一个阶段带过，但不许整段不拍；不得低于{max(55, fast_target - 25)}秒"} if fast else {}),
            "stages_per_clip": f"{STAGE_RANGE[0]}-{STAGE_RANGE[1]}",
            "clip_seconds": f"20-{int(MAX_CLIP_SECONDS)}",
            "episode_seconds": f"about 90, max {int(EPISODE_SECONDS_MAX)}",
            "spoken_chars_total": f"{SPOKEN_RANGE[0]}-{SPOKEN_RANGE[1]}",
            "turn_text_max_chars": TURN_MAX_CHARS,
            "source_quote_chars": f"{QUOTE_MIN_CHARS}-{QUOTE_MAX_CHARS}",
            "max_skipped_segments": MAX_SKIPPED,
            "one_visible_speaker_per_shot": True,
            "no_narration_no_inner_voice": True,
            "recap_usage": "previous_volumes_recap 是前面几十章的主线走向、previous_chapters_recap 是最近几章的细节，两者都只用于保持连续性（人物关系、所在位置、状态），本集只拍当前章的事件，不得把前情内容拍进来",
            **({"episode_seconds_min": args.min_seconds} if args.min_seconds else {}),
        },
        **({"director_notes": args.notes} if args.notes else {}),
    }
    print(json.dumps({"segments": [{k: v for k, v in s.items() if k != "text"} for s in segments], "characters": names, "locations": list(location_map), "bible_size": [len(full_bible.characters), len(full_bible.locations)], "recap_chapters": [r.get("chapter") for r in previous_recap], "visual_grammar": (grammar or {}).get("name"), "profile": profile}, ensure_ascii=False), flush=True)
    if args.dry_run:
        atomic_write_json(episode_dir / "request_dry_run.json", payload)
        return 0

    started = time.monotonic()
    attempts: list[dict] = []
    repair: dict | None = None
    final_errors: list[str] = []
    result = None
    patch_rounds = 0  # small repair calls used so far on this chapter
    patch_seconds_left = PATCH_TOTAL_SECONDS
    for attempt in range(1, args.max_redo + 2):
        request_payload = {**payload, **({"repair": repair} if repair else {})}
        atomic_write_json(episode_dir / f"request_attempt_{attempt:02d}.json", request_payload)
        raw_path = episode_dir / f"response_attempt_{attempt:02d}.raw.json"
        if args.replay and attempt == 1:
            content = Path(args.replay).read_text(encoding="utf-8")
            meta = {"replayed_from": str(args.replay)}
        else:
            content, meta = call_model(base_url=args.base_url, model=args.model, payload=request_payload, schema=schema, max_tokens=args.max_tokens, timeout=args.timeout, notes=args.notes, grammar=grammar, profile=profile, fast=fast)
        raw_path.write_text(content, encoding="utf-8")
        if meta.get("analysis"):
            (episode_dir / f"analysis_attempt_{attempt:02d}.txt").write_text(meta.pop("analysis"), encoding="utf-8")
        try:
            if re.search(r"\s{2000,}", content):
                raise ValueError("constrained decoding derailed into whitespace (finish_reason=%s)" % meta.get("finish_reason"))
            raw = extract_json(content)
        except (json.JSONDecodeError, ValueError) as error:
            final_errors = [f"response is not one JSON object: {type(error).__name__}: {error}"]
            attempts.append({"attempt": attempt, **meta, "errors": final_errors})
            repair = {"validation_errors": final_errors}
            if meta.get("finish_reason") == "length":
                # The draft was cut off, not malformed: without saying so the
                # next attempt is just as long and fails the same way.
                repair["instruction"] = ("上一稿超过输出长度上限被截断。本次压缩篇幅：每个字段只写必要内容，camera 和 light 在机位或光源不变时写"
                                         "\"同上\"，avoid 每段不超过 3 项，台词句子不加长；不得减少区段覆盖。")
            continue
        errors, warnings, shots = validate_and_normalize(raw, segments, bible, location_map, episode.source_text, everyone)
        fingerprint = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        resent = attempts and attempts[-1].get("fingerprint") == fingerprint
        attempts.append({"attempt": attempt, **meta, "errors": errors, "warnings": warnings, "fingerprint": fingerprint, "resent_previous": bool(resent)})
        print(json.dumps({"attempt": attempt, **meta, "error_count": len(errors), "warning_count": len(warnings)}, ensure_ascii=False), flush=True)
        if errors and attempt == args.max_redo + 1 and all("低于本次要求的下限" in e for e in errors):
            # The length floor is a preference, not a gate: on the last redo a
            # slightly short chapter is accepted and the shortfall reported.
            warnings = [*warnings, *("report only: " + e for e in errors)]
            errors = []
            attempts[-1]["errors"] = []
            attempts[-1]["floor_waived"] = True
        patchable = patchable_errors(errors) if errors else None
        while patchable and patch_rounds < PATCH_ROUNDS and patch_seconds_left > 0:
            # Nearly every redo trigger in the trial was local - a forgotten
            # segment, a blood word in one start_state, a paraphrased quote, a
            # missing speaker.  Fix those stages with one small call and re-check
            # instead of a 150-650 s re-plan that tends to break something else.
            patch_rounds += 1
            missing_ids, faulty = patchable
            patch_timeout = min(PATCH_TIMEOUT_SECONDS, patch_seconds_left)
            summary = {"round": patch_rounds, "segments": missing_ids, "stages": list(faulty), "timeout_seconds": round(patch_timeout, 1)}
            print(json.dumps({"attempt": attempt, "patch": summary, "status": "patching"}, ensure_ascii=False), flush=True)
            patch_started = time.monotonic()
            try:
                patched = patch_plan(raw, missing_ids, faulty, segments, names, list(location_map), timeout=patch_timeout)
            except Exception as error:  # noqa: BLE001 - fall through to the normal redo
                failure = {**summary, "failed": f"{type(error).__name__}: {str(error)[:120]}", "elapsed_seconds": round(time.monotonic() - patch_started, 1)}
                attempts[-1].setdefault("patches", []).append(failure)
                print(json.dumps({"attempt": attempt, "patch": failure}, ensure_ascii=False), flush=True)
                break
            finally:
                patch_seconds_left = max(0.0, patch_seconds_left - (time.monotonic() - patch_started))
            errors, warnings, shots = validate_and_normalize(patched, segments, bible, location_map, episode.source_text, everyone)
            attempts[-1].setdefault("patches", []).append({**summary, "errors_after": len(errors), "errors": errors[:6]})
            print(json.dumps({"attempt": attempt, "patch": summary, "error_count": len(errors)}, ensure_ascii=False), flush=True)
            raw = patched  # the next round, or the full redo, starts from the improved draft
            patchable = patchable_errors(errors) if errors else None
        if not errors and STRICT_PLAN:
            strict = strict_plan_errors(shots, episode.source_text, segments, raw)
            if strict and attempt == args.max_redo + 1:
                warnings = [*warnings, *("report only (strict gate waived on the last attempt): " + e for e in strict)]
                attempts[-1]["strict_waived"] = strict
            elif strict:
                errors = strict
                attempts[-1]["errors"] = strict
                print(json.dumps({"attempt": attempt, "strict_errors": strict}, ensure_ascii=False), flush=True)
        if errors:
            final_errors = errors
            repair = {
                "instruction": "上一稿未通过硬门检查。逐条修复 validation_errors，其余内容尽量保持不变；source_quote 必须从对应区段逐字复制。",
                "validation_errors": errors,
                "previous_response": raw,
            }
            if resent:
                # The model echoed its previous draft; feedback is not landing.
                # Withhold the draft so it has to write the plan again.
                repair.pop("previous_response")
                repair["instruction"] = "上一稿被原样重发，未做任何修改。本次不提供上一稿，请按 validation_errors 里的数字要求从头重写一份符合规模的剧本。"
            continue
        result = (raw, shots, warnings)
        break

    if result is None:
        failure = {
            "status": "planning_failed",
            "policy": POLICY,
            "episode_index": episode.index,
            "attempts": attempts,
            "errors": final_errors,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }
        atomic_write_json(episode_dir / "planning_failed.json", failure)
        print(json.dumps({"status": "planning_failed", "errors": final_errors}, ensure_ascii=False, indent=2))
        return 2

    raw, shots, warnings = result
    # A new script invalidates everything downstream: the packed plan must be
    # rebuilt and a finished-episode report from the old plan would make batch
    # drivers skip the episode as done.
    for stale in ("clip_plan.json", "clip_plan.md", "thin_media_report.json", "media_qc_report.json"):
        (episode_dir / stale).unlink(missing_ok=True)
    skipped = {str(item.get("segment_id")): str(item.get("reason", "")) for item in (raw.get("skipped_segments") or []) if isinstance(item, dict)}
    report_metrics = metrics(shots, episode.source_text, segments, skipped)
    plan = to_episode_plan(raw, shots, location_map, episode.source_text, episode.source_title)
    report = {
        "status": "passed",
        "policy": POLICY,
        "model": args.model,
        "episode_index": episode.index,
        "source_text_sha256": sha256_text(episode.source_text),
        "style_fingerprint": bible.style_fingerprint,
        "hard_gates": {"source_coverage": "passed", "cast_and_speakers": "passed"},
        "metrics": report_metrics,
        "warnings": [*warnings, *soft_warnings(report_metrics)],
        "attempts": attempts,
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    atomic_write_json(episode_dir / "chapter_script.json", {"video_title": raw.get("video_title"), "source_title": episode.source_title, "episode_index": episode.index, "profile": profile, "hook": raw.get("hook"), "summary": raw.get("summary"), "clip_count": len(raw.get("clips") or []), "shots": shots, "skipped_segments": skipped})
    atomic_write_json(episode_dir / "chapter_script_report.json", report)
    with open(recap_path.with_suffix(".lock"), "w") as lock:  # planners may run in parallel
        fcntl.flock(lock, fcntl.LOCK_EX)
        recap = json.loads(recap_path.read_text(encoding="utf-8")) if recap_path.is_file() else []
        recap = [row for row in recap if int(row.get("chapter", 0)) != episode.index]
        recap.append({"chapter": episode.index, "title": episode.source_title, "summary": raw.get("summary"), "hook": raw.get("hook")})
        atomic_write_json(recap_path, sorted(recap, key=lambda row: int(row.get("chapter", 0))))
    record_cast(novel_dir, episode.index,
                sorted({name for shot in shots for name in shot.get("characters", []) or []}),
                sorted({shot["location"] for shot in shots if shot.get("location")}))
    atomic_write_json(episode_dir / "episode_plan.json", plan.model_dump(mode="json"))
    (episode_dir / "chapter_script.md").write_text(render_markdown(raw, shots, report, episode.source_title), encoding="utf-8")
    print(json.dumps({"status": "passed", "episode_dir": str(episode_dir), "metrics": report_metrics, "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
