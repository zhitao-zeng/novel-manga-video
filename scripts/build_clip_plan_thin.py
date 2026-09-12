#!/usr/bin/env python3
"""Pack thin chapter shots into Seedance 2.5 clips and write staged prompts.

Reads <episode_dir>/chapter_script.json (from plan_chapter_thin.py) plus the
StoryBible.  Consecutive shots are packed into clips of at most 30 seconds
and at most 6 stages; a clip is cut on a location change, when it would
exceed 30 seconds, or, once it is already long enough, when the chapter
segment changes.  Each clip gets one prompt in the official Seedance 2.5
layout: 【生成目标】, per-material bindings (用于 / 不采用), 【阶段n】 with
开始时 / 主要事件 / 声音 / 结束时, then style, camera, sound and 【保持一致】.
Title-card shots become separate card segments rendered in post.
Writes clip_plan.json and clip_plan.md.  No model call, no remote call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from pathlib import Path

from novel_manga.models import StoryBible
from thin_phases import chapter_of, load_phases, phase_for, phase_labels, phased  # noqa: E402
from novel_manga.util import atomic_write_json

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thin_profile import frame_spec, is_fast, load_genre, load_profile, plan_fingerprint

POLICY = "thin-clip-plan-v12-six-stages" + ("-15s" if os.environ.get("NOVEL_CLIP_SECONDS_MAX", "").strip() in {"15", "15.0"} else "")
TWO_VIEW_CAST_LIMIT = 2
LEAD_ROLES = {"主角", "女主角", "男主角"}
# Seedance sometimes burns its own caption bar into the picture; the film has its own
# subtitle track, so every prompt forbids it explicitly.
NO_SUBTITLES = "不要在画面上生成字幕条、台词字幕、字幕栏、说明文字或任何叠加的文字条"
GENRE_REJECTS: list[str] = []  # from the genre preset; appended to 【不要】
GENRE_CROWD = ""
# NOVEL_CLIP_SECONDS_MAX=15 is the sd2.0 lane (its reference-to-video mode
# stops at 15 s): shorter clips, three stages at most, an earlier soft cut.
MAX_CLIP_SECONDS = float(os.environ.get("NOVEL_CLIP_SECONDS_MAX", "30") or 30)
SOFT_CUT_SECONDS = 18.0 if MAX_CLIP_SECONDS > 15 else round(MAX_CLIP_SECONDS * 0.6, 1)
MAX_STAGES = 6 if MAX_CLIP_SECONDS > 15 else 3
STRIP_PUNCT = r"[\s　，。！？；：、…—,.!?;:\"“”'‘’（）()]"
STAGE_LABELS = ["一", "二", "三", "四", "五", "六"]
ANON_VOICE = {
    "无名测验员": "画外的中年测验员（男声）",
    "无名族人": "画外一名楚家族人",
    "无名少年": "画外一名少年",
    "无名少女": "画外一名少女",
    "无名群声": "画外的人群",
}


def spoken_chars(value: str) -> int:
    return len(re.sub(STRIP_PUNCT, "", value or ""))


def compact(value: str, limit: int | None = None) -> str:
    text = re.sub(r"\s+", "", value or "").strip("；。")
    if limit is not None and len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def shot_seconds(shot: dict) -> float:
    seconds = 1.0
    for turn in shot["turns"]:
        mode = turn["delivery_mode"]
        if mode in {"visible_dialogue", "offscreen_dialogue"}:
            seconds += spoken_chars(turn["text"]) / 4.0 + 1.0
        elif mode == "silent_action":
            seconds += 3.0
        elif mode == "chat_message":
            # card mode: the message lives on our chat card, the clip only
            # shows the reaction - one beat, not the reading time
            seconds += 1.0 if str(CHAT_SCREEN.get("render", "card")) == "card" else spoken_chars(turn["text"]) / 5.0 + 1.5
        elif mode == "singing":
            seconds += 6.0
    return max(3.0, round(seconds, 2))


def is_title_card(shot: dict) -> bool:
    return all(turn["delivery_mode"] == "title_card" for turn in shot["turns"])


PACKER_VERSION = "thin-packer-2026-09-12+split-keeps-cast"
# "planned": every rule below cuts (today's behaviour).  "execution": only the rules the
# video service enforces cut - location, length cap, stage ceiling - and the planner's
# clip_hint and the source-segment boundary are recorded but not acted on.
PACK_MODE = os.environ.get("NOVEL_PACK_MODE", "execution").strip() or "execution"
EXECUTION_RULES = ("location", "duration", "stage_limit")
DECISIONS: list[dict] = []  # observation only; written to pack_decisions.json by main()


def _cut_checks(current: dict, last: dict, shot: dict, seconds: float) -> dict[str, bool]:
    """Every cut condition the packer tests, in the order it tests them."""
    return {
        "location": shot["location"] != current["location"],
        "clip_hint": bool(shot.get("clip_hint") and shot.get("clip_hint") != last.get("clip_hint")),
        "duration": current["seconds"] + seconds > MAX_CLIP_SECONDS,
        "stage_limit": len(current["shots"]) >= MAX_STAGES,
        "source_chunk": bool(not shot.get("clip_hint") and current["seconds"] >= SOFT_CUT_SECONDS
                             and shot["segment_id"] != last["segment_id"]),
    }


SENTENCE_END = re.compile(r"(?<=[。！？!?…；;])")
CLAUSE_END = re.compile(r"(?<=[，、,])")


def text_chunks(text: str, limit: int) -> list[str]:
    """`text` in consecutive pieces of at most `limit` spoken characters: cut at sentence ends, a sentence that is
    still too long at commas, and a clause that is still too long anywhere.  Nothing is dropped or reordered."""
    units: list[str] = []
    for sentence in (s for s in SENTENCE_END.split(text) if s):
        if spoken_chars(sentence) <= limit:
            units.append(sentence)
            continue
        for clause in (c for c in CLAUSE_END.split(sentence) if c):
            while spoken_chars(clause) > limit:
                units.append(clause[:limit])
                clause = clause[limit:]
            if clause:
                units.append(clause)
    chunks: list[str] = []
    for unit in units:
        if chunks and spoken_chars(chunks[-1] + unit) <= limit:
            chunks[-1] += unit
        else:
            chunks.append(unit)
    return chunks


def split_long_shot(shot: dict) -> list[dict]:
    """A stage too long for one clip, as consecutive stages that each fit one.

    The packer cuts only between stages, so a stage longer than the cap used to become a clip of its own whose
    request was simply clamped: 星海 484's 71-second stage went out as a 15-second request and 45 % of its lines
    were never spoken.  Its turns are dealt out in order into parts that fit - a line too long for any clip is
    first cut at sentence ends - and every part keeps the stage's picture, the later ones carrying on from it."""
    if is_title_card(shot) or shot_seconds(shot) <= MAX_CLIP_SECONDS:
        return [shot]
    limit = max(8, int((MAX_CLIP_SECONDS - 2.0) * 4))  # one line alone: 1 s for the stage + 1 s + chars / 4
    turns = [{**turn, "text": piece} if piece != turn["text"] else turn
             for turn in shot["turns"]
             for piece in (text_chunks(turn["text"], limit)
                           if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"} and spoken_chars(turn["text"]) > limit
                           else [turn["text"]])]
    parts: list[list[dict]] = []
    for turn in turns:
        if parts and shot_seconds({**shot, "turns": parts[-1] + [turn]}) <= MAX_CLIP_SECONDS:
            parts[-1].append(turn)
        else:
            parts.append([turn])
    if len(parts) == 1:
        return [shot]  # one silent action longer than a clip: nothing to cut at
    DECISIONS.append({"kind": "split_stage", "stage": shot.get("origin_index"), "seconds": round(shot_seconds(shot), 2),
                      "parts": len(parts), "cap": MAX_CLIP_SECONDS})
    pieces = []
    picture = "".join(str(shot.get(key, "")) for key in ("visual_prompt", "motion_prompt", "end_state"))
    on_screen = [name for name in shot.get("characters", []) if name in picture]
    for number, part in enumerate(parts, 1):
        piece = {**shot, "turns": part, "split_part": [number, len(parts)]}
        if number > 1:
            # The stage's action happens once, in its first part, which ends where the stage ends; the later parts
            # carry on from there and only finish the lines.  Copying the action into every part had a character
            # push the same door open three times.
            end = compact(shot.get("end_state", ""))
            text = f"承接上一段结束时的画面：{end}" if end else "承接上一段结束时的画面"
            # Who was in the picture stays in it.  clip_cast keeps a silent character of a crowded stage only when the
            # stage text names them, and with the stage's own text gone they dropped to the background, card and all
            # (星海 706: 金曜 and 伊芙 at the table in part 1, gone from parts 2 and 3).  Named only where clip_cast would
            # drop them - more than two listed - so every other part keeps its wording, and its rendered takes.
            speakers = [t["speaker_name"] for t in part if t.get("delivery_mode") == "visible_dialogue" and t.get("speaker_name")]
            listed = list(dict.fromkeys([*shot.get("characters", []), *speakers]))
            kept = [name for name in on_screen if name not in text and name not in speakers]
            if len(listed) > 2 and kept:
                text += "；" + "、".join(kept) + "仍在画面中"
            piece["visual_prompt"] = text
            piece["motion_prompt"] = "人物保持上一段结束时的位置和姿态，接着把话说完，不重复上一段的动作"
        pieces.append(piece)
    return pieces


def pack(shots: list[dict]) -> list[dict]:
    DECISIONS.clear()
    clips: list[dict] = []
    current: dict | None = None
    for shot in [piece for shot in shots for piece in split_long_shot(shot)]:
        if is_title_card(shot):
            if current:
                clips.append(current)
                current = None
            clips.append({"kind": "title_card", "location": shot["location"], "shots": [shot], "seconds": 3.0})
            continue
        seconds = shot_seconds(shot)
        if seconds > MAX_CLIP_SECONDS:
            # Only a single action longer than a whole clip is left like this (split_long_shot cuts at turns).
            DECISIONS.append({"kind": "single_stage_over_cap", "stage": shot.get("origin_index"),
                              "seconds": round(seconds, 2), "cap": MAX_CLIP_SECONDS})
        if current is not None:
            last = current["shots"][-1]
            checks = _cut_checks(current, last, shot, seconds)
            acted = {name: hit for name, hit in checks.items() if PACK_MODE != "execution" or name in EXECUTION_RULES}
            cut = any(acted.values())  # in "planned" mode this is the same disjunction as before
            if cut:
                violated = [name for name, hit in checks.items() if hit]
                DECISIONS.append({
                    "kind": "cut", "after_stage": last.get("origin_index"), "before_stage": shot.get("origin_index"),
                    "decision_reason": next(name for name, hit in acted.items() if hit), "violated_constraints": violated,
                    "candidate_seconds": round(current["seconds"] + seconds, 2),
                    "candidate_stages": len(current["shots"]) + 1,
                })
                clips.append(current)
                current = None
        if current is None:
            current = {"kind": "video", "location": shot["location"], "shots": [], "seconds": 0.0}
        current["shots"].append(shot)
        current["seconds"] = round(current["seconds"] + seconds, 2)
    if current:
        clips.append(current)
    return absorb_small_clips(clips)


MIN_STANDALONE_SECONDS = 8.0


def absorb_small_clips(clips: list[dict]) -> list[dict]:
    """A leftover clip under 8 s joins the neighbour it fits into (next first)."""
    result: list[dict] = list(clips)
    changed = True
    while changed:
        changed = False
        for index, clip in enumerate(result):
            if clip["kind"] != "video" or clip["seconds"] >= MIN_STANDALONE_SECONDS:
                continue
            for neighbour_index in (index + 1, index - 1):
                if not 0 <= neighbour_index < len(result):
                    continue
                neighbour = result[neighbour_index]
                if (
                    neighbour["kind"] == "video"
                    and neighbour["location"] == clip["location"]
                    and neighbour["seconds"] + clip["seconds"] <= MAX_CLIP_SECONDS
                    and len(neighbour["shots"]) + len(clip["shots"]) <= MAX_STAGES
                ):
                    shots = clip["shots"] + neighbour["shots"] if neighbour_index > index else neighbour["shots"] + clip["shots"]
                    DECISIONS.append({"kind": "absorbed", "small_clip_seconds": clip["seconds"],
                                      "into": "next" if neighbour_index > index else "previous",
                                      "merged_seconds": round(neighbour["seconds"] + clip["seconds"], 2)})
                    neighbour["shots"] = shots
                    neighbour["seconds"] = round(neighbour["seconds"] + clip["seconds"], 2)
                    result.pop(index)
                    changed = True
                    break
            if changed:
                break
    return result


TERMINAL_PUNCT = "。！？…!?"
SFX_ONLY = re.compile(r"^[\u4e00-\u9fff]{1,5}声$")


def merged_turns(shot: dict) -> list[dict]:
    """Rejoin pieces of one line that the planner split at a comma.

    A piece whose predecessor ended mid-sentence (comma, no terminal
    punctuation) is a continuation of the same line.  Separate crowd lines end
    with terminal punctuation and stay separate voices.  Offscreen "turns" that
    are really a sound label (e.g. 狼嚎声) become sfx instead of speech.
    """
    merged: list[dict] = []
    extra_sfx: list[str] = []
    for turn in shot["turns"]:
        text = turn["text"].strip()
        if turn["delivery_mode"] == "offscreen_dialogue" and SFX_ONLY.match(text):
            extra_sfx.append(text)
            continue
        if (
            merged
            and turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"}
            and merged[-1]["delivery_mode"] == turn["delivery_mode"]
            and merged[-1]["speaker_name"] == turn["speaker_name"]
            and merged[-1]["text"].rstrip()[-1:] not in TERMINAL_PUNCT
        ):
            merged[-1] = {**merged[-1], "text": merged[-1]["text"] + text}
        else:
            merged.append({**turn, "text": text})
    if extra_sfx:
        shot["sfx"] = "，".join([*(x for x in [shot.get("sfx", "")] if x), *extra_sfx])
    return merged


ORDINALS = ["一名", "另一名", "第三名", "第四名", "第五名"]

LIGHT_NOUNS = re.compile(r"(月光|月色|银月|灯|烛|火光|篝火|阳光|日光|晨光|夕阳|余晖|天光|窗|光芒|金光|纹路的光|灵碑.{0,4}光)")
CAMERA_MOVE = re.compile(r"(推近|推进|拉远|拉开|摇镜|横移|跟拍|环绕|航拍|俯瞰|变焦|甩镜|手持晃动)")
ABSTRACT = re.compile(r"(气质|一丝|氛围|电影感|仿佛|宛如|犹如|似乎|莫名|难以言喻|无法形容)")
READABLE_TEXT = re.compile(r"(大字|写着|字样|显示[“\"]|刻着[“\"])")


def lint_stage(shot: dict) -> list[str]:
    """Restraint test: the stage must still stand once adjectives are removed."""
    issues = []
    start, event, end = shot.get("visual_prompt", ""), shot.get("motion_prompt", ""), shot.get("end_state", "")
    camera, light = shot.get("camera", ""), shot.get("light", "")
    if not light and not LIGHT_NOUNS.search(start):
        issues.append("no_light_source")
    if not camera:
        issues.append("no_camera_position")
    if len(compact(camera)) > 60 or len(compact(light)) > 60:
        issues.append("camera_or_light_over_60_chars")
    if CAMERA_MOVE.search(camera + event + start):
        issues.append("camera_move_words")
    if ABSTRACT.search(start + event + end):
        issues.append("abstract_wording")
    if READABLE_TEXT.search(start + event + end + light) and not chat_turns(shot):
        issues.append("readable_text")
    if len(compact(start)) > 120:
        issues.append("start_state_over_120_chars")
    if not any(t["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue", "silent_action", "chat_message", "singing"} for t in shot["turns"]):
        issues.append("no_audible_or_visible_action")
    return issues


def load_grammar(path: Path | None, episode_dir: Path) -> dict | None:
    candidate = path or (episode_dir.parent / "visual_grammar.json")
    if candidate and candidate.is_file():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def chat_turns(shot: dict) -> list[dict]:
    return [t for t in shot["turns"] if t["delivery_mode"] == "chat_message" and t["text"].strip()]


CHAT_SCREEN: dict = {
    "app": "微信群聊", "group_name": "", "self_name": "",
    # "card": chat_card.py draws the screen and the runner cuts it in; "video": the old way, Seedance writes the text.
    "render": "card",
    "layout": "顶部居中显示群名；消息按时间从上到下排列；每条消息左侧一个圆形卡通头像，昵称以一行小字显示在气泡上方，气泡内只有消息正文；"
              "他人的消息是白色气泡靠左，本人的消息是绿色气泡靠右且不显示昵称；底部是输入栏；界面简洁干净，字体为清晰的简体中文黑体、字号偏大",
}


VOICES: dict[str, str] = {}  # character -> series_assets/voices/<name>.wav, from the voice bank


def load_voices(novel_dir: Path) -> dict[str, str]:
    """Reference voices built by build_voices_thin.py; empty until the first episodes exist."""
    manifest = novel_dir / "series_assets" / "voices" / "voices.json"
    if manifest.is_file():
        try:
            rows = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            rows = {}
        for name in rows:
            if (novel_dir / "series_assets" / "voices" / f"{name}.wav").is_file():
                VOICES[name] = f"series_assets/voices/{name}.wav"
    return VOICES


def load_chat_screen(novel_dir: Path) -> dict:
    """Per-novel chat UI template (outputs/<novel>/chat_screen.json): same group
    name and layout in every clip of every episode."""
    path = novel_dir / "chat_screen.json"
    if path.is_file():
        CHAT_SCREEN.update({k: v for k, v in json.loads(path.read_text(encoding="utf-8")).items() if k in CHAT_SCREEN and v})
    return CHAT_SCREEN


def screen_clause(shot: dict) -> str:
    """What the phone screen shows: the chat template plus the messages, verbatim,
    as the only readable text the video may contain.  Sender names go above the
    bubble, never inside it."""
    turns = chat_turns(shot)
    if not turns:
        return ""
    if str(CHAT_SCREEN.get("render", "card")) == "card":
        # The messages are drawn by chat_card.py and cut in as their own segment;
        # asking the video model for legible Chinese only produces garbled text.
        return ("屏幕内容：不要拍屏幕内容——手机或电脑屏幕背对镜头、被手指遮住或只见反光，屏幕上不出现任何文字；"
                "镜头给看屏幕的人的表情和动作。")
    me = CHAT_SCREEN.get("self_name", "")
    bubbles = []
    for t in turns:
        who, text = t["speaker_name"], t["text"].strip()
        if me and who == me:
            bubbles.append(f"本人{who}的绿色气泡靠右，正文「{text}」")
        else:
            bubbles.append(f"昵称「{who}」显示在气泡上方，白色气泡正文「{text}」")
    group = f"群名「{CHAT_SCREEN['group_name']}」，" if CHAT_SCREEN.get("group_name") else ""
    return (f"屏幕内容：手机屏幕特写占画面主体、屏幕正对镜头，{CHAT_SCREEN['app']}界面（{group}{CHAT_SCREEN['layout']}），"
            f"依次弹出{len(turns)}条消息，气泡内只有正文、不带任何括号或昵称，文字为清晰可读的简体中文、无乱码、与下列内容逐字一致：{'；'.join(bubbles)}。")


def sound_clause(shot: dict) -> str:
    parts = []
    seen: dict[str, int] = {}
    for turn in merged_turns(shot):
        mode = turn["delivery_mode"]
        emotion = turn.get("emotion") or "平静"
        who = turn["speaker_name"]
        if mode == "visible_dialogue":
            parts.append(f"中文普通话，{emotion}，{who}开口说：{{{turn['text']}}}")
        elif mode == "singing":
            manner = turn["text"].strip() or "轻声哼唱一段温柔的无词旋律"
            parts.append(f"{who}{manner}：原创的、没有歌词的哼唱，不是任何已有歌曲，口型为哼唱，音量柔和")
        elif mode == "offscreen_dialogue":
            voice = ANON_VOICE.get(who, f"画外的{who}")
            if who in {"无名族人", "无名少年", "无名少女"}:
                nth = seen.get(who, 0)
                seen[who] = nth + 1
                voice = voice.replace("一名", ORDINALS[min(nth, len(ORDINALS) - 1)], 1)
            parts.append(f"中文普通话，{emotion}，{voice}说：{{{turn['text']}}}，画面中无人开口")
    if shot.get("sfx") and compact(shot["sfx"]) not in {"无", "没有", "无声", "无音效", "空", "none"}:
        parts.append(f"<{shot['sfx']}>")
    if chat_turns(shot):
        parts.append("<手机消息提示音>")
    if not parts:
        parts.append("只有环境声，无人说话")
    return "；".join(parts)


def clip_cast(clip: dict) -> list[str]:
    """Named actors who need a reference image in this clip.

    An actor earns a reference by speaking on camera or by being named in a
    stage's picture text.  A silent extra who is merely listed as present
    (a guest at the far table) gets no image: with two similar-looking women
    referenced, the video model swaps costumes between them.  Such extras are
    still described in the stage text as background.
    """
    listed: list[str] = []
    active: set[str] = set()
    for shot in clip["shots"]:
        for name in shot["characters"]:
            if name not in listed:
                listed.append(name)
        picture = "".join(str(shot.get(k, "")) for k in ("visual_prompt", "motion_prompt", "end_state"))
        for name in shot["characters"]:
            if name in picture:
                active.add(name)
        for turn in shot["turns"]:
            if turn["delivery_mode"] == "visible_dialogue" and turn["speaker_name"]:
                active.add(turn["speaker_name"])
                if turn["speaker_name"] not in listed:
                    listed.append(turn["speaker_name"])
    if len(listed) <= 2:
        return listed
    kept = [name for name in listed if name in active]
    clip["background_only"] = [name for name in listed if name not in active]
    return kept or listed


def anchor_of(name: str, bible: StoryBible, limit: int = 34, character=None, prefer: tuple[str, ...] = ()) -> str:
    """A few words that tell this character apart, for the prompt to say out loud.  `character` stands in for
    the bible entry: the phase's look, when this chapter has one (thin_phases)."""
    character = character or next((c for c in bible.characters if c.name == name), None)
    if character is None:
        return ""
    for field in (*prefer, "silhouette", "hair", "palette", "appearance"):  # a phase names its changed field first
        value = str(getattr(character, field, "") or "").strip()
        if value in {"无", "none", "-", "无特殊", "暂无"} or len(value) < 6:
            continue  # a placeholder is worse than saying nothing
        if value:
            text = value.replace("\n", " ")
            cut = text[:limit]
            end = max(cut.rfind(mark) for mark in "。，；,;")
            return cut[:end] if end > limit // 2 else cut
    return ""


def build_references(cast: list[str], location_short: str, bible: StoryBible, location_map: dict[str, str], speakers: tuple[str, ...] = (), novel_dir: Path | None = None, chapter: int | None = None) -> tuple[list[dict], list[str], str]:
    character_index = {character.name: index for index, character in enumerate(bible.characters, start=1)}
    location_index = {full.split("：", 1)[0].strip(): index for index, full in enumerate(bible.locations, start=1)}
    # The leads carry the story and were the most often face- or costume-swapped
    # in review; they always get their expressions sheet as a second view when
    # it exists on disk, whatever the tier.
    leads = {character.name for character in bible.characters if str(character.role or "") in LEAD_ROLES}
    references: list[dict] = []
    bindings: list[str] = []
    count = 0
    # Two views per actor sharpen identity, but a crowded shot would then carry
    # ten reference images and the model starts blending faces.  Past two named
    # actors, give each one its turnaround only.
    two_views = len(cast) <= TWO_VIEW_CAST_LIMIT
    # A character with phases (series_assets/phases.json) references the card of the phase this chapter is in,
    # and the anchor describes that look - 沈玄川 is white-haired from ch1406, his base card is not.
    phases = load_phases(novel_dir) if novel_dir is not None else {}
    by_name = {character.name: character for character in bible.characters}
    for name in cast:
        phase = phase_for(phases, name, chapter)
        asset = str(phase["asset_id"]) if phase else f"character_{character_index[name]:03d}"
        look = phased(by_name[name], phase)
        changed = tuple(f for f in ("hair", "appearance", "silhouette", "palette") if phase and phase.get(f))
        count += 1
        first = count
        references.append({"tag": f"@图片{first}", "role": "character", "name": name, "asset_id": asset, "path": f"series_assets/characters/{asset}/turnaround.jpeg",
                           **({"phase": str(phase.get("label", ""))} if phase else {})})
        sheet = novel_dir is not None and (novel_dir / "series_assets" / "characters" / asset / "expressions.jpeg").is_file()
        lead_sheet = sheet and name in leads
        # A base card's sheet is drawn on demand by the renderer; a phase card's only by build_phase_cards.py
        # --expressions, so a variant is referenced by its turnaround alone until the sheet exists.
        if (two_views and (phase is None or sheet)) or lead_sheet:
            count += 1
            second = count
            references.append({"tag": f"@图片{second}", "role": "character", "name": name, "asset_id": asset, "path": f"series_assets/characters/{asset}/expressions.jpeg"})
            anchor = anchor_of(name, bible, character=look, prefer=changed)
            bindings.append(
                f"<{name}>对应@图片{first}和@图片{second}：@图片{first}定五官、发型、年龄感和肤色，"
                f"@图片{second}定身体比例、服装版型、主色和配饰；两张都不采用背景、姿势和构图；"
                # The single-view binding always said this; the two-view one - every lead - did not, and 408 of
                # 雾月's 574 doppelganger clips show an extra person wearing the lead's face.
                "画面中其他任何人都不得使用这两张图的相貌、发型或服装，该角色只能出现一次"
                + (f"。{name}的辨识特征：{anchor}" if anchor else ""))
        else:
            anchor = anchor_of(name, bible, character=look, prefer=changed)
            bindings.append(
                f"<{name}>只对应@图片{first}，只采用五官、发型、体型和服装，不采用图片背景、姿势和构图；"
                "不得把该角色的长相用在其他人身上"
                + (f"。{name}的辨识特征：{anchor}" if anchor else ""))
    full = location_map[location_short]
    location_asset = f"location_{location_index[location_short]:03d}"
    count += 1
    references.append({"tag": f"@图片{count}", "role": "location", "name": location_short, "asset_id": location_asset, "path": f"series_assets/locations/{location_asset}/establishing.jpeg"})
    # One reference voice per speaking character that has one in the bank.  The
    # model listens to the references and matches them to the on-screen speakers
    # by itself; it ignores both @音频N text bindings and the order of the audio
    # items (docs/seedance-reference-audio.md), so nothing about voices goes
    # into the prompt.  Cast order here is just for a stable plan file.
    for voice_index, name in enumerate((n for n in cast if n in speakers and n in VOICES), start=1):
        references.append({"tag": f"@音频{voice_index}", "role": "voice", "name": name, "path": VOICES[name]})
    description = compact(full.split("：", 1)[1] if "：" in full else full, 40)
    location_binding = f"@图片{count}用于<{location_short}>的建筑、地面、固定道具和光线（{description}），不采用图中人物"
    return references, bindings, location_binding


# 头 counts animals, so 三头龙 usually means "three dragons" - and a video model reads it as "a
# three-headed dragon" and draws one.  Two or more dragons take 条, two or more 雪铠 take 只; a count of
# one is left alone (one head is normal), and so is anything not followed by those nouns, such as 三头犬.
# Some dragons do have several heads - in 星海, one fused from three young dragons, and 赛洛斯, written
# 赛洛斯三头龙 - so a prompt is left as written when it counts heads (三个脑袋, 三颗头颅) or opens a
# clause with a cast name and the count.  A name inside a list (洛恩、科特林、赛洛斯三头龙) still counts
# dragons.
_HEAD_COUNT = re.compile(r"(两|二|三|四|五|六|七|八|九|十|几|数|[2-9])头(?=[^，。；、{}\s]{0,4}?(龙|雪铠))")
_COUNTED_HEADS = re.compile(r"[两二三四五六七八九几数多][颗个](头|脑袋)")
_CLAUSE_START = r'(?:^|(?<=[，。；：“”"‘’（(【\s]))'


def plain_counts(prompt: str, cast: list[str] = ()) -> str:
    """Rewrite animal counts outside the spoken lines, which sit inside {} and stay as written."""
    parts = re.split(r"(\{[^}]*\})", prompt)
    direction = "".join(part for part in parts if not part.startswith("{"))
    if _COUNTED_HEADS.search(direction) or any(
            re.search(_CLAUSE_START + re.escape(name) + _HEAD_COUNT.pattern, direction, re.M) for name in cast):
        return prompt
    return "".join(part if part.startswith("{") else
                   _HEAD_COUNT.sub(lambda m: m.group(1) + ("条" if m.group(2) == "龙" else "只"), part)
                   for part in parts)


def compile_prompt(clip: dict, bible: StoryBible, cast: list[str], bindings: list[str], location_binding: str, grammar: dict | None = None, frame: dict | None = None) -> str:
    frame = frame or frame_spec({"frame": "9:16"})
    shots = clip["shots"]
    cast_text = "、".join(cast) if cast else "无具名人物"
    start = compact(shots[0]["motion_prompt"], 30)
    end = compact(shots[-1]["end_state"], 30)
    lines = [
        f"【生成目标】生成一段{frame['text']}的中国国漫短剧片段，约{clip['request_seconds']}秒。核心主体是{cast_text}，主要事件是从“{start}”到“{end}”。"
    ]
    if bindings:
        lines.append("【人物】" + "。".join(bindings) + "。")
    if cast:
        # 133 of 613 flagged clips in review had people who were never cast.
        others = "；远处模糊背景里只允许" + "、".join(clip["background_only"]) if clip.get("background_only") else ""
        lines.append(f"【人数】画面中始终只有这{len(cast)}位人物：{cast_text}；无名角色只在画外发声、不入镜；不出现任何未列出的人（老者、路人、随从、背景人物都不要）{others}。")
    if clip.get("identity_notes"):
        lines.append("【身份区分】" + compact(clip["identity_notes"]) + "。")
    if clip.get("background_only"):
        lines.append("【远景人物】" + "、".join(clip["background_only"]) + "只作远处模糊背景，不入近景、不开口，本段不提供他们的参考图；不得把他们的长相或服饰用在有参考图的角色身上。")
    lines.append("【场景】" + location_binding + "。")
    if grammar:
        axes = "；".join(
            f"{label}：{grammar[key]}" for label, key in (("光影与对比", "light_contrast"), ("色彩与曝光", "color_exposure"), ("镜头与机位", "lens_camera"), ("构图与空间", "composition_space")) if grammar.get(key)
        )
        if axes:
            lines.append("【视觉语法】" + axes + "。")
    for index, shot in enumerate(shots):
        label = STAGE_LABELS[index]
        if index == 0:
            head = f"{shot['shot_scale']}开场。开始时：{compact(shot['visual_prompt'])}"
        else:
            head = f"切至{shot['shot_scale']}。承接上一阶段：{compact(shots[index - 1]['end_state'])}。画面：{compact(shot['visual_prompt'])}"
        def carried(field: str, label: str) -> str:
            value = compact(shot.get(field, ""))
            if not value:
                return ""
            previous = compact(shots[index - 1].get(field, "")) if index else ""
            if value in {"同上", previous} and index:
                return f"{label}同上。"
            return f"{label}：{value}。"
        witness = carried("camera", "机位")
        source_light = carried("light", "光源")
        lines.append(
            f"【阶段{label}·{shot['shot_scale']}】{head}。{witness}{source_light}主要事件：{compact(shot['motion_prompt'])}。"
            f"{screen_clause(shot)}声音：{sound_clause(shot)}。结束时：{compact(shot['end_state'])}。"
        )
    scales = "、".join(dict.fromkeys(shot["shot_scale"] for shot in shots))
    ambience = list(dict.fromkeys(shot["sfx"] for shot in shots if shot.get("sfx")))
    ambience_text = "、".join(ambience) if ambience else "现场环境声"
    lines.append(f"画面呈现{(grammar or {}).get('style_line') or bible.visual_style}。")
    lines.append(f"镜头采用{frame['text']}，按阶段切换景别（{scales}），每个阶段开始时切一次画面，阶段内机位固定不运镜；{frame['composition']}；同一场景内保持人物左右位置和视线方向不变。")
    lines.append(f"声音包括角色对白、<{ambience_text}>和与动作同步的音效；无背景音乐。")
    if any(chat_turns(shot) for shot in shots) and str(CHAT_SCREEN.get("render", "card")) != "card":
        group = f"群名「{CHAT_SCREEN['group_name']}」、" if CHAT_SCREEN.get("group_name") else ""
        lines.append(f"【保持一致】保持人物身份、数量、服装、固定道具位置、空间方向和声音关系稳定；除手机屏幕上的{group}昵称和指定的聊天消息外，画面中不出现其他文字、数字、字幕、Logo或水印；屏幕上的消息文字必须与指定内容逐字一致、简体中文、无乱码，昵称在气泡上方而不在气泡内；聊天界面在各阶段保持同一布局；不出现血液和伤口；不新增具名人物。")
    else:
        lines.append("【保持一致】保持人物身份、数量、服装、固定道具位置、空间方向和声音关系稳定；画面中不出现任何文字、数字、Logo或水印；不出现血液和伤口；不新增具名人物。")
    avoid = [compact(shot.get("avoid", "")) for shot in shots if shot.get("avoid")]
    avoid = list(dict.fromkeys(a for a in avoid if a))
    rejects = [str(r) for r in (grammar or {}).get("rejects", []) if r]
    if str(CHAT_SCREEN.get("render", "card")) == "card":
        # The chat screen is drawn by us now, so the "except the chat messages"
        # exemption in the genre and grammar rejects no longer applies.
        rejects = [r.replace("（手机屏幕上剧本指定的聊天消息除外）", "") for r in rejects]
        globals()["GENRE_REJECTS"] = [r.replace("（手机屏幕上剧本指定的聊天消息除外）", "") for r in GENRE_REJECTS]
    lines.append("【不要】" + "；".join([*avoid, *GENRE_REJECTS, *rejects, NO_SUBTITLES]) + "。")
    return plain_counts("\n".join(lines), cast)


def load_context(episode_dir: Path, bible_path: Path, grammar_path: Path | None = None, style: str | None = None,
                 frame: str | None = None, tier: str | None = None) -> dict:
    """Everything an episode's clip entries are built from besides the shots - and the module settings the packer
    reads (genre rejects, crowd line, anonymous voices, chat screen, voice bank, cards per character)."""
    global GENRE_REJECTS, GENRE_CROWD, TWO_VIEW_CAST_LIMIT
    bible = StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
    grammar = load_grammar(grammar_path, episode_dir)
    load_chat_screen(episode_dir.parent)
    load_voices(episode_dir.parent)
    profile = load_profile(episode_dir.parent, style=style, frame=frame, tier=tier)
    genre = load_genre(profile)
    GENRE_REJECTS = [x for x in [genre.get("era_rejects", "")] + list(genre.get("grammar_rejects_extra", [])) if x]
    GENRE_CROWD = genre.get("crowd_default", "")
    ANON_VOICE.update(genre.get("anon_voice") or {})  # off-screen voice descriptions for the genre's anonymous roles
    if is_fast(profile):
        TWO_VIEW_CAST_LIMIT = 0  # one reference card per character
    overrides_path = episode_dir / "clip_overrides.json"
    return {
        "episode_dir": episode_dir, "bible": bible, "grammar": grammar, "profile": profile, "frame": frame_spec(profile),
        "location_map": {full.split("：", 1)[0].strip(): full for full in bible.locations},
        "overrides": json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.is_file() else {},
    }


def prepared_shots(script: dict, episode_dir: Path) -> list[dict]:
    """The script's shots as the packer reads them: nicknames resolved to canonical names, "同上" camera and light
    filled in from the last concrete value, and every shot numbered."""
    shots = script["shots"]
    aliases_path = episode_dir.parent / "bible_aliases.json"
    aliases = json.loads(aliases_path.read_text(encoding="utf-8")) if aliases_path.is_file() else {}
    if aliases:  # a nickname in the script must resolve to the canonical card
        for shot in shots:
            shot["characters"] = list(dict.fromkeys(aliases.get(n, n) for n in shot.get("characters", [])))
            for turn in shot.get("turns", []):
                turn["speaker_name"] = aliases.get(turn.get("speaker_name", ""), turn.get("speaker_name", ""))
    # "同上" is only meaningful inside one prompt.  Resolve it (and blanks)
    # from the last concrete value in reading order so that the first stage of
    # every clip states its light and camera explicitly.
    last: dict[str, str] = {}
    for shot in shots:
        for field in ("camera", "light"):
            value = compact(shot.get(field, ""))
            if value and value != "同上":
                last[field] = value
            elif last.get(field):
                shot[field] = last[field]
    for index, shot in enumerate(shots, start=1):
        shot.setdefault("index", index)
    return shots


def clip_entry(clip: dict, clip_id: str, ctx: dict, override: dict | None = None) -> dict:
    """The plan entry of one packed clip (a title card or a video clip); `override` defaults to the episode's
    clip_overrides.json entry for `clip_id`."""
    shot_indexes = [shot["index"] for shot in clip["shots"]]
    if clip["kind"] == "title_card":
        return {
            "clip_id": clip_id,
            "kind": "title_card",
            "shot_indexes": shot_indexes,
            "seconds_estimate": clip["seconds"],
            "request_seconds": 3,
            "text": "\n".join(turn["text"] for turn in clip["shots"][0]["turns"]),
        }
    bible = ctx["bible"]
    clip["request_seconds"] = int(min(MAX_CLIP_SECONDS, max(4, math.ceil(clip["seconds"]))))
    cast = clip_cast(clip)
    override = ctx["overrides"].get(clip_id, {}) if override is None else override
    if override.get("cast"):
        # Director fix: restrict the reference set (e.g. drop a silent
        # look-alike) so the video model cannot blend two faces.
        cast = [name for name in override["cast"] if name in {c.name for c in bible.characters}]
        for shot in clip["shots"]:
            shot["characters"] = [n for n in shot["characters"] if n in cast]
    if override.get("extra_avoid"):
        for shot in clip["shots"]:
            shot["avoid"] = "；".join(x for x in (shot.get("avoid", ""), override["extra_avoid"]) if x)
    clip["identity_notes"] = override.get("identity_notes", "")
    speakers = tuple(dict.fromkeys(
        turn["speaker_name"] for shot in clip["shots"] for turn in shot["turns"]
        if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue", "singing"} and turn.get("speaker_name")
    ))
    references, bindings, location_binding = build_references(cast, clip["location"], bible, ctx["location_map"], speakers=speakers, novel_dir=ctx["episode_dir"].parent, chapter=chapter_of(ctx["episode_dir"]))
    prompt = compile_prompt(clip, bible, cast, bindings, location_binding, ctx["grammar"], ctx["frame"])
    lint = {shot["index"]: lint_stage(shot) for shot in clip["shots"]}
    lint = {k: v for k, v in lint.items() if v}
    lines = [
        {"speaker_name": turn["speaker_name"], "delivery_mode": turn["delivery_mode"], "text": turn["text"]}
        for shot in clip["shots"]
        for turn in merged_turns(shot)
        if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"}
    ]
    return {
        "clip_id": clip_id,
        "kind": "video",
        "location": clip["location"],
        "shot_indexes": shot_indexes,
        "segment_ids": list(dict.fromkeys(shot["segment_id"] for shot in clip["shots"])),
        "stage_count": len(clip["shots"]),
        "seconds_estimate": clip["seconds"],
        "request_seconds": clip["request_seconds"],
        "cast": cast,
        "references": references,
        "lines": lines,
        "chat_lines": [{"speaker_name": t["speaker_name"], "text": t["text"].strip(), "chat_target": str(t.get("chat_target") or "").strip()} for shot in clip["shots"] for t in chat_turns(shot)],
        "spoken_text": "".join(line["text"] for line in lines),
        "prompt": prompt,
        "prompt_chars": len(prompt),
        "lint": lint,
        "override": override or None,
        "background_only": clip.get("background_only", []),
    }


def plan_totals(clips: list[dict], shots: list[dict], ctx: dict) -> dict:
    video_clips = [clip for clip in clips if clip["kind"] == "video"]
    totals = {
        "clip_count": len(clips),
        "video_clip_count": len(video_clips),
        "title_card_count": len(clips) - len(video_clips),
        "shot_count": len(shots),
        "estimated_seconds": round(sum(clip["seconds_estimate"] for clip in clips), 1),
        "requested_seconds": sum(clip["request_seconds"] for clip in clips),
        "mean_stages_per_clip": round(sum(clip["stage_count"] for clip in video_clips) / max(1, len(video_clips)), 2),
        "max_prompt_chars": max((clip["prompt_chars"] for clip in video_clips), default=0),
        "lint_stage_count": sum(len(clip.get("lint", {})) for clip in video_clips),
        "lint_by_code": {code: sum(list(v).count(code) for clip in video_clips for v in clip.get("lint", {}).values()) for code in ("no_light_source", "no_camera_position", "camera_or_light_over_60_chars", "camera_move_words", "abstract_wording", "readable_text", "start_state_over_120_chars", "no_audible_or_visible_action")},
        "visual_grammar": (ctx["grammar"] or {}).get("name"),
        "profile": ctx["profile"],
    }
    totals["lint_by_code"] = {k: v for k, v in totals["lint_by_code"].items() if v}
    return totals


def carry_corrections(old_plan: dict | None, plan: dict, feedback_path: Path) -> dict:
    """Keep each director correction on the clip it was written for.

    Corrections are keyed by clip id, and a new plan's ids can name other clips (a re-plan, a re-pack): a note about
    乙 landed on the clip that now shows 甲.  A note stays only where the new plan has the very clip it was written
    for - the same prompt - under that clip's id there; the others are set aside beside it.  Returns those."""
    if not feedback_path.is_file():
        return {}
    try:
        notes = json.loads(feedback_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    old_prompts = {clip.get("clip_id"): clip.get("prompt") for clip in (old_plan or {}).get("clips", [])}
    new_ids: dict[str, str] = {}
    for clip in plan.get("clips", []):
        if clip.get("prompt"):
            new_ids.setdefault(clip["prompt"], clip["clip_id"])
    kept, dropped = {}, {}
    for clip_id, note in notes.items():
        target = new_ids.get(old_prompts.get(clip_id) or "")
        if target:
            kept[target] = note
        else:
            dropped[clip_id] = note
    if dropped:
        atomic_write_json(feedback_path.with_name(f"review_feedback.set-aside-{time.strftime('%Y%m%d-%H%M%S')}.json"),
                          {"reason": "the clip plan was written again and these clips are not in it", "notes": dropped})
    if kept != notes:
        atomic_write_json(feedback_path, kept)
    return dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--bible", type=Path, required=True)
    parser.add_argument("--grammar", type=Path, help="visual_grammar.json; defaults to <novel dir>/visual_grammar.json when present")
    parser.add_argument("--style", choices=("2d", "3d"), help="override profile.json style")
    parser.add_argument("--frame", choices=("9:16", "16:9"), help="override profile.json frame")
    parser.add_argument("--tier", choices=("quality", "fast"), help="override profile.json tier")
    args = parser.parse_args()
    episode_dir = args.episode_dir.resolve()
    ctx = load_context(episode_dir, args.bible, args.grammar, args.style, args.frame, args.tier)
    shots = prepared_shots(json.loads((episode_dir / "chapter_script.json").read_text(encoding="utf-8")), episode_dir)
    clips = [clip_entry(clip, f"clip_{number:02d}", ctx) for number, clip in enumerate(pack(shots), start=1)]
    totals = plan_totals(clips, shots, ctx)
    plan = {"policy": POLICY, "phases": phase_labels(clips), "limits": {"max_clip_seconds": MAX_CLIP_SECONDS, "soft_cut_seconds": SOFT_CUT_SECONDS, "max_stages": MAX_STAGES}, "totals": totals, "clips": clips}
    try:
        old_plan = json.loads((episode_dir / "clip_plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old_plan = None
    atomic_write_json(episode_dir / "clip_plan.json", plan)
    carry_corrections(old_plan, plan, episode_dir / "review_feedback.json")
    # Observation only: why the packer cut where it did.  clip_plan.json is unchanged by this.
    atomic_write_json(episode_dir / "pack_decisions.json", {
        "packer_version": PACKER_VERSION, "pack_mode": PACK_MODE,
        "limits": {"max_clip_seconds": MAX_CLIP_SECONDS, "max_stages": MAX_STAGES, "soft_cut_seconds": SOFT_CUT_SECONDS,
                   "min_standalone_seconds": MIN_STANDALONE_SECONDS},
        "decisions": list(DECISIONS),
    })
    report_path = episode_dir / "thin_media_report.json"
    if report_path.is_file():
        # The runner stamps the plan it rendered; a report for a different plan
        # would let a batch driver skip this episode as finished.
        stamped = json.loads(report_path.read_text(encoding="utf-8")).get("clip_plan_fingerprint")
        current = plan_fingerprint(plan)
        if stamped and stamped != current:  # reports from before the stamp are trusted
            report_path.unlink()
            (episode_dir / "media_qc_report.json").unlink(missing_ok=True)
            print(json.dumps({"note": "clip plan changed; stale thin_media_report.json removed"}, ensure_ascii=False))
    md = [f"# 片段计划（{POLICY}）", "", f"{totals['video_clip_count']} 段视频，{totals['shot_count']} 镜，预计 {totals['estimated_seconds']} 秒，申请 {totals['requested_seconds']} 秒", "", "| 片段 | 镜 | 阶段数 | 预计秒 | 申请秒 | 人物 | 台词 |", "|---|---|---|---|---|---|---|"]
    for clip in clips:
        if clip["kind"] != "video":
            md.append(f"| {clip['clip_id']} | {clip['shot_indexes']} | 字幕卡 | {clip['seconds_estimate']} | {clip['request_seconds']} | | {clip['text']} |")
            continue
        md.append(f"| {clip['clip_id']} | {clip['shot_indexes'][0]}–{clip['shot_indexes'][-1]} | {clip['stage_count']} | {clip['seconds_estimate']} | {clip['request_seconds']} | {'、'.join(clip['cast'])} | {len(clip['lines'])} 条 |")
    md.append("")
    for clip in clips:
        if clip["kind"] != "video":
            continue
        md.append(f"## {clip['clip_id']} · 镜 {clip['shot_indexes'][0]}–{clip['shot_indexes'][-1]} · {clip['request_seconds']} 秒")
        md.append("参考图：" + "；".join(f"{ref['tag']}={ref['path']}" for ref in clip["references"]))
        md.append("")
        md.append("```")
        md.append(clip["prompt"])
        md.append("```")
        if clip.get("lint"):
            md.append("提示词检查（只报告）：" + "；".join(f"镜{k}: {', '.join(v)}" for k, v in clip["lint"].items()))
        md.append("")
    (episode_dir / "clip_plan.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"totals": totals, "clips": [{k: clip[k] for k in ("clip_id", "shot_indexes", "seconds_estimate", "request_seconds") if k in clip} | {"cast": clip.get("cast", [])} for clip in clips]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
