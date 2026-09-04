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
from thin_profile import is_fast, FRAMES, STYLE_NAME, frame_spec, load_profile

POLICY = "thin-chapter-plan-v8.4-fast-coverage-target"
SEGMENT_COUNT = 8
TURN_MAX_CHARS = 26
QUOTE_MIN_CHARS = 8
QUOTE_MAX_CHARS = 200
MAX_SKIPPED = 3
SHOT_RANGE = (12, 24)
CLIP_RANGE = (3, 4)
STAGE_RANGE = (4, 6)
MAX_CLIP_SECONDS = 30.0
CLIP_SECONDS_TOLERANCE = 1.0
EPISODE_SECONDS_MAX = 105.0
EPISODE_SECONDS_MIN = 0.0  # set by --min-seconds
SPOKEN_RANGE = (220, 300)
MIN_SPOKEN_CHARS = 220
ANONYMOUS_SPEAKERS = ["无名测验员", "无名族人", "无名少年", "无名少女", "无名群声"]
SCENE_JOBS = ["建立", "推进", "对峙", "揭示", "反转", "决定", "收束"]
SHOT_SCALES = ["特写", "近景", "中近景", "中景", "全景"]
DELIVERY_MODES = ["visible_dialogue", "offscreen_dialogue", "silent_action", "title_card", "chat_message", "singing"]
CHAT_MAX_CHARS = 24
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
            seconds += spoken_chars(str(turn.get("text", ""))) / 5.0 + 1.5
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


def chapter_quotes(text: str) -> list[str]:
    quotes = re.findall(r"[“\"]([^”\"]{2,120})[”\"]", text)
    return list(dict.fromkeys(quote.strip() for quote in quotes if spoken_chars(quote) >= 2))

SYSTEM_PROMPT = """你是中文{frame_text}{style_name}短剧的编剧兼分镜师。把"当前章"改编成一集约90秒的短剧，由3到4段可用视频模型一次生成的连续片段组成，只输出一个JSON对象。
输出结构：clips，3到4段。每段clip在同一地点内连续拍摄，时长20到30秒，由4到6个"阶段"stages组成；每个阶段3到7秒，只有一个主要变化和最多两句台词，写清开始时、主要事件、结束时能直接看到的状态。相邻阶段用不同景别切画面（全景、中景、近景、特写交替）。
时长预算是硬约束：每个发声汉字0.25秒，每句台词加1秒，每个阶段加1秒，无声动作阶段按4秒；单段不得超过30秒，全集不得超过100秒。全集发声字数控制在220到300字之间。
硬规则：
1. 只用当前章的事实、人物和顺序。不得引入后文信息、新事件、新地点，或StoryBible之外的具名角色。
2. 原文已切成8个连续区段 seg_1 到 seg_8。每个阶段必须写 segment_id，并把该区段里一段连续原文逐字复制到 source_quote（8到120字；不得改字、不得拼接）。每个区段至少被一个阶段引用；纯景物或纯议论的区段可以跳过，写进 skipped_segments 并给理由，最多跳过3个。
3. 成片没有旁白、没有内心独白。可听的只有四种：visible_dialogue（画内可见说话者，一个阶段只允许一个可见说话者）、offscreen_dialogue（画外声：群众议论、测验员喊话等）、silent_action（无声的可见动作或反应，text写动作）、title_card（时间或地点跳转的字幕卡，只在必要时用）。另有一种不发声的 chat_message：手机或电脑屏幕上显示的聊天消息，speaker_name 写发消息的人，text 写消息原文，逐字取自原文、不超过24字（长消息只取前半句）；一个阶段最多三条；含 chat_message 的阶段，start_state 和 event 必须写明手机屏幕特写、屏幕正对镜头、消息气泡清晰可读，以及看手机的人的反应。原文里的群聊内容优先用 chat_message 呈现，不要改成画外音。唱歌场景用 singing：speaker_name 写唱歌的人，text 只写演唱方式（如"轻声哼唱一段温柔的无词旋律"），绝不写任何歌词、歌名或已有歌曲，观众的反应用其他阶段的画面和画外音表现。silent_action只能写此刻能拍到的动作，不能用来表达回忆、心理活动、气质评价或规则说明。
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

ANALYSIS_INSTRUCTION = (
    "先做内部规划，不要输出JSON：第一步定时长预算，全集约90秒分成3到4段，每段写出覆盖哪些区段、几个阶段、估算秒数；"
    "第二步定台词取舍，列出保留的原文台词（合计220到300字，可删子句）、合并或删掉的群众议论、以及必须用一两句画外议论外化的叙述事实（写出改成谁说的什么话）；"
    "第三步列每个阶段的景别和主要动作。不超过800字。"
)

ANALYSIS_INSTRUCTION = (
    "先做内部规划，不要输出JSON：逐区段列出必须保留的引号台词、必须外化成台词的叙述事实（写出改成谁说的什么话）、"
    "此刻可拍的动作；然后给出片段划分：每段覆盖哪些区段、几个阶段、估算秒数，总共6到10段。不超过800字。"
)


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
        "required": ["speaker_name", "delivery_mode", "text", "emotion"],
        "properties": {
            "speaker_name": {"type": "string", "enum": [*character_names, *ANONYMOUS_SPEAKERS, ""]},
            "delivery_mode": {"type": "string", "enum": DELIVERY_MODES},
            "text": {"type": "string"},
            "emotion": {"type": "string"},
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
            "turns": {"type": "array", "minItems": 1, "items": turn},
        },
    }
    clip = {
        "type": "object",
        "additionalProperties": False,
        "required": ["clip_id", "location", "characters", "avoid", "stages"],
        "properties": {
            "clip_id": {"type": "string"},
            "location": {"type": "string", "enum": location_names},
            "characters": {"type": "array", "items": {"type": "string", "enum": character_names}},
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
            "clips": {"type": "array", "minItems": 1, "items": clip},
            "skipped_segments": {
                "type": "array",
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


def _post(client: httpx.Client, base_url: str, headers: dict, request: dict) -> dict:
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


def call_model(*, base_url: str, model: str, payload: dict, schema: dict, max_tokens: int, timeout: float, analysis_tokens: int = 2500, notes: str = "", grammar: dict | None = None, profile: dict | None = None, fast: bool = False) -> tuple[str, dict]:
    frame = frame_spec(profile) if profile else FRAMES["9:16"]
    system_prompt = SYSTEM_PROMPT.replace("{frame_text}", frame["text"]).replace("{style_name}", STYLE_NAME[(profile or {}).get("style", "2d")])
    system_prompt += f"\n\n【画幅】{frame['text']}。{frame['composition']}。"
    if grammar:
        system_prompt += f"\n\n【全书视觉语法，camera 和 light 字段必须与之一致】{grammar_text(grammar)}"
    system_prompt += (f"\n\n【本章导演意见，优先于一般偏好】{notes}" if notes else "")
    headers = {}
    api_key = os.getenv("QWEN38_LOCAL_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    user_content = json.dumps(payload, ensure_ascii=False)
    started = time.monotonic()
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        if fast:
            # Fast tier keeps a short think-pass: dropping it made first drafts
            # miss coverage or come out as 30 s stubs, and the redos cost more
            # than the pass saved once planning ran in parallel.
            analysis_body = _post(client, base_url, headers, {
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
          analysis_body = _post(client, base_url, headers, {
              "model": model,
              "temperature": 0.3,
              "max_tokens": analysis_tokens,
              "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True, "reasoning_effort": "low"},
              "messages": [
                  {"role": "system", "content": system_prompt + "\n\n" + ANALYSIS_INSTRUCTION},
                  {"role": "user", "content": user_content},
              ],
          })
          analysis_message = analysis_body["choices"][0]["message"]
          analysis = str(analysis_message.get("content") or analysis_message.get("reasoning") or "")[-6000:]
          analysis_seconds = round(time.monotonic() - started, 1)
        body = _post(client, base_url, headers, {
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


def canonical(name: str) -> str:
    return ALIASES.get(str(name).strip(), str(name).strip())


def validate_and_normalize(raw: dict, segments: list[dict], bible: StoryBible, location_map: dict[str, str], chapter_text: str) -> tuple[list[str], list[str], list[dict]]:
    errors: list[str] = []
    warnings: list[str] = []
    names = [character.name for character in bible.characters]
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
        full_line = key in {quote_key(q) for q in chapter_quotes(chapter_text)}
        # Length is judged on what the model wrote (punctuation included), the
        # same count it was given in the schema; the key is only for matching.
        if len(re.sub(r"[\s\u3000]+", "", quote)) < QUOTE_MIN_CHARS and not full_line:
            errors.append(f"{position}: source_quote too short, need at least {QUOTE_MIN_CHARS} chars: {quote!r}")
        elif len(key) > QUOTE_MAX_CHARS:
            errors.append(f"{position}: source_quote too long, at most {QUOTE_MAX_CHARS} chars")
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
                elif speaker.startswith("无名"):
                    warnings.append(f"{position}: anonymous {speaker} cannot be visible; converted to offscreen")
                    mode = "offscreen_dialogue"
                else:
                    errors.append(f"{position}: visible speaker {speaker!r} is not a StoryBible character")
            elif mode == "offscreen_dialogue":
                if not speaker:
                    errors.append(f"{position}: offscreen_dialogue needs speaker_name")
                elif speaker not in names and not speaker.startswith("无名"):
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
                if len(compact(text)) > CHAT_MAX_CHARS:
                    # A long message is cut, not rejected: the bubble just shows its first clause.
                    cut = text[:CHAT_MAX_CHARS].rstrip("，,、；;：:")
                    warnings.append(f"{position}: chat_message {len(compact(text))} 字，截为 {cut!r}")
                    text = cut
            else:
                errors.append(f"{position}: unknown delivery_mode {mode!r}")
                continue
            pieces = split_turn_text(text) if mode in {"visible_dialogue", "offscreen_dialogue"} else [text]
            if len(pieces) > 1:
                warnings.append(f"{position}: turn of {spoken_chars(text)} chars split into {len(pieces)}")
            for piece in pieces:
                turns_out.append({"speaker_name": speaker, "delivery_mode": mode, "text": piece, "emotion": emotion})
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
                    errors.append(
                        f"{position}: {field} 含{label}描述（{match.group(0)}），图片和视频都不允许；"
                        "去掉血迹和伤口，碑上的结果改写为无字的发光纹路"
                    )
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
            "去掉只有反应没有事件的无声阶段；每个阶段最多两句短台词。不得为了缩短而删掉整个区段：每个区段仍须至少被一个阶段引用或写进 skipped_segments。不得原样重发上一稿。"
        )
    skipped_raw = raw.get("skipped_segments") or []
    skipped = {str(item.get("segment_id")): str(item.get("reason", "")) for item in skipped_raw if isinstance(item, dict)}
    for segment_id in list(skipped):
        if segment_id in cited:
            warnings.append(f"{segment_id} listed as skipped but also cited; skip ignored")
            skipped.pop(segment_id)
    if len(skipped) > MAX_SKIPPED:
        errors.append(f"too many skipped segments ({len(skipped)} > {MAX_SKIPPED}): {sorted(skipped)}")
    uncited = [s["segment_id"] for s in segments if s["segment_id"] not in cited and s["segment_id"] not in skipped]
    if FAST_TIER and uncited and len(uncited) + len(skipped) <= MAX_SKIPPED:
        for segment_id in uncited:  # fast tier: a forgotten segment counts as skipped
            skipped[segment_id] = "快速档：未引用，自动记为跳过"
        warnings.append(f"report only: 快速档自动跳过未引用的区段 {uncited}")
        uncited = []
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
    parser.add_argument("--base-url", default=os.getenv("QWEN38_LOCAL_BASE_URL", "http://127.0.0.1:18120/v1"))
    parser.add_argument("--model", default=os.getenv("QWEN38_LOCAL_MODEL", "Qwen3.8-27B-Project"))
    # A healthy clip plan is 4.5-6.5K tokens.  Constrained decoding can derail
    # into endless whitespace; a tight cap turns that into a fast, cheap redo.
    parser.add_argument("--max-tokens", type=int, default=9000)
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
    main_cast = [c for c in full_bible.characters if "主角" in c.role]
    present = [c for c in full_bible.characters if c.name and c.name in chapter_text]
    sliced_characters = list({c.name: c for c in [*main_cast, *present]}.values()) or full_bible.characters[:8]
    sliced_locations = [full for full in full_bible.locations if full.split("：", 1)[0].strip() in chapter_text] or full_bible.locations[-6:]
    bible = full_bible.model_copy(update={"characters": sliced_characters, "locations": sliced_locations})
    location_map = {full.split("：", 1)[0].strip(): full for full in bible.locations}
    names = [character.name for character in bible.characters]

    novel_dir = Path(args.output_root).resolve() / args.novel_id
    episode_dir = novel_dir / f"{args.novel_id}_{episode.index}"
    profile = load_profile(novel_dir, style=args.style, frame=args.frame, tier=args.tier)
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
        global CLIP_RANGE, SPOKEN_RANGE, EPISODE_SECONDS_MAX
        fast_target = int(min(100, max(60, round(episode.text_count / 3000 * 60 / 10) * 10)))
        CLIP_RANGE = (2, 3) if fast_target <= 60 else (3, 4)
        SPOKEN_RANGE = (140, 220) if fast_target <= 60 else (200, 300)
        EPISODE_SECONDS_MAX = 130.0
        EPISODE_SECONDS_MIN = max(EPISODE_SECONDS_MIN, fast_target - 25)  # soft: waived on the last redo
        if episode.text_count > 4000:
            MAX_SKIPPED = 4  # merged chapters cannot cover all eight segments in ~90 s
    aliases_path = novel_dir / "bible_aliases.json"
    ALIASES.update(json.loads(aliases_path.read_text(encoding="utf-8")) if aliases_path.is_file() else {})
    grammar_path = args.grammar or (novel_dir / "visual_grammar.json")
    grammar = json.loads(grammar_path.read_text(encoding="utf-8")) if grammar_path.is_file() else None
    episode_dir.mkdir(parents=True, exist_ok=True)
    bible_target = novel_dir / "story_bible.json"
    if not bible_target.is_file():
        shutil.copy2(args.bible, bible_target)

    segments = split_segments(episode.source_text, episode.source_title, SEGMENT_COUNT)
    atomic_write_json(episode_dir / "segments.json", segments)
    # Rolling recap: the summaries the planner itself wrote for the previous
    # chapters, for continuity only (who is where, what just happened).
    recap_path = novel_dir / "recap.json"
    recap = json.loads(recap_path.read_text(encoding="utf-8")) if recap_path.is_file() else []
    previous_recap = [row for row in recap if int(row.get("chapter", 0)) < episode.index][-5:]
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
        **({"previous_chapters_recap": previous_recap} if previous_recap else {}),
        "segments": [{"segment_id": s["segment_id"], "text": s["text"]} for s in segments],
        "quoted_lines_that_must_be_kept": chapter_quotes(episode.source_text),
        "requirements": {
            "clip_count": f"{CLIP_RANGE[0]}-{CLIP_RANGE[1]}",
            **({"episode_target": f"约{fast_target}秒，{CLIP_RANGE[0]}到{CLIP_RANGE[1]}段，每段3到5个阶段；8个区段每个至少用一个阶段带到（长对话压成一两句，群众议论合并），不得低于{max(45, fast_target - 20)}秒"} if fast else {}),
            "stages_per_clip": f"{STAGE_RANGE[0]}-{STAGE_RANGE[1]}",
            "clip_seconds": f"20-{int(MAX_CLIP_SECONDS)}",
            "episode_seconds": f"about 90, max {int(EPISODE_SECONDS_MAX)}",
            "spoken_chars_total": f"{SPOKEN_RANGE[0]}-{SPOKEN_RANGE[1]}",
            "turn_text_max_chars": TURN_MAX_CHARS,
            "source_quote_chars": f"{QUOTE_MIN_CHARS}-{QUOTE_MAX_CHARS}",
            "max_skipped_segments": MAX_SKIPPED,
            "one_visible_speaker_per_shot": True,
            "no_narration_no_inner_voice": True,
            "recap_usage": "previous_chapters_recap 只用于保持连续性（人物关系、所在位置、状态），本集只拍当前章的事件，不得把前情内容拍进来",
            **({"episode_seconds_min": args.min_seconds} if args.min_seconds else {}),
        },
        **({"director_notes": args.notes} if args.notes else {}),
    }
    print(json.dumps({"segments": [{k: v for k, v in s.items() if k != "text"} for s in segments], "characters": names, "locations": list(location_map), "bible_size": [len(full_bible.characters), len(full_bible.locations)], "recap_chapters": [r.get("chapter") for r in previous_recap], "visual_grammar": (grammar or {}).get("name"), "profile": profile}, ensure_ascii=False))
    if args.dry_run:
        atomic_write_json(episode_dir / "request_dry_run.json", payload)
        return 0

    started = time.monotonic()
    attempts: list[dict] = []
    repair: dict | None = None
    final_errors: list[str] = []
    result = None
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
            continue
        errors, warnings, shots = validate_and_normalize(raw, segments, bible, location_map, episode.source_text)
        fingerprint = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        resent = attempts and attempts[-1].get("fingerprint") == fingerprint
        attempts.append({"attempt": attempt, **meta, "errors": errors, "warnings": warnings, "fingerprint": fingerprint, "resent_previous": bool(resent)})
        print(json.dumps({"attempt": attempt, **meta, "error_count": len(errors), "warning_count": len(warnings)}, ensure_ascii=False))
        if errors and attempt == args.max_redo + 1 and all("低于本次要求的下限" in e for e in errors):
            # The length floor is a preference, not a gate: on the last redo a
            # slightly short chapter is accepted and the shortfall reported.
            warnings = [*warnings, *("report only: " + e for e in errors)]
            errors = []
            attempts[-1]["errors"] = []
            attempts[-1]["floor_waived"] = True
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
    atomic_write_json(episode_dir / "episode_plan.json", plan.model_dump(mode="json"))
    (episode_dir / "chapter_script.md").write_text(render_markdown(raw, shots, report, episode.source_title), encoding="utf-8")
    print(json.dumps({"status": "passed", "episode_dir": str(episode_dir), "metrics": report_metrics, "elapsed_seconds": report["elapsed_seconds"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
