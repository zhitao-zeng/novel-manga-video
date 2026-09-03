#!/usr/bin/env python3
"""Pack thin chapter shots into Seedance 2.5 clips and write staged prompts.

Reads <episode_dir>/chapter_script.json (from plan_chapter_thin.py) plus the
StoryBible.  Consecutive shots are packed into clips of at most 30 seconds
and at most 5 stages; a clip is cut on a location change, when it would
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
import re
import sys
from pathlib import Path

from novel_manga.models import StoryBible
from novel_manga.util import atomic_write_json

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thin_profile import frame_spec, load_profile, plan_fingerprint

POLICY = "thin-clip-plan-v8.1-batch-ready"
TWO_VIEW_CAST_LIMIT = 2
MAX_CLIP_SECONDS = 30.0
SOFT_CUT_SECONDS = 18.0
MAX_STAGES = 6
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
    return max(3.0, round(seconds, 2))


def is_title_card(shot: dict) -> bool:
    return all(turn["delivery_mode"] == "title_card" for turn in shot["turns"])


def pack(shots: list[dict]) -> list[dict]:
    clips: list[dict] = []
    current: dict | None = None
    for shot in shots:
        if is_title_card(shot):
            if current:
                clips.append(current)
                current = None
            clips.append({"kind": "title_card", "location": shot["location"], "shots": [shot], "seconds": 3.0})
            continue
        seconds = shot_seconds(shot)
        if current is not None:
            last = current["shots"][-1]
            cut = (
                shot["location"] != current["location"]
                or (shot.get("clip_hint") and shot.get("clip_hint") != last.get("clip_hint"))
                or current["seconds"] + seconds > MAX_CLIP_SECONDS
                or len(current["shots"]) >= MAX_STAGES
                or (
                    not shot.get("clip_hint")
                    and current["seconds"] >= SOFT_CUT_SECONDS
                    and shot["segment_id"] != last["segment_id"]
                )
            )
            if cut:
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
                    and len(neighbour["shots"]) + len(clip["shots"]) <= MAX_STAGES + 1
                ):
                    shots = clip["shots"] + neighbour["shots"] if neighbour_index > index else neighbour["shots"] + clip["shots"]
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
    if READABLE_TEXT.search(start + event + end + light):
        issues.append("readable_text")
    if len(compact(start)) > 120:
        issues.append("start_state_over_120_chars")
    if not any(t["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue", "silent_action"} for t in shot["turns"]):
        issues.append("no_audible_or_visible_action")
    return issues


def load_grammar(path: Path | None, episode_dir: Path) -> dict | None:
    candidate = path or (episode_dir.parent / "visual_grammar.json")
    if candidate and candidate.is_file():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def sound_clause(shot: dict) -> str:
    parts = []
    seen: dict[str, int] = {}
    for turn in merged_turns(shot):
        mode = turn["delivery_mode"]
        emotion = turn.get("emotion") or "平静"
        who = turn["speaker_name"]
        if mode == "visible_dialogue":
            parts.append(f"中文普通话，{emotion}，{who}开口说：{{{turn['text']}}}")
        elif mode == "offscreen_dialogue":
            voice = ANON_VOICE.get(who, f"画外的{who}")
            if who in {"无名族人", "无名少年", "无名少女"}:
                nth = seen.get(who, 0)
                seen[who] = nth + 1
                voice = voice.replace("一名", ORDINALS[min(nth, len(ORDINALS) - 1)], 1)
            parts.append(f"中文普通话，{emotion}，{voice}说：{{{turn['text']}}}，画面中无人开口")
    if shot.get("sfx"):
        parts.append(f"<{shot['sfx']}>")
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


def build_references(cast: list[str], location_short: str, bible: StoryBible, location_map: dict[str, str]) -> tuple[list[dict], list[str], str]:
    character_index = {character.name: index for index, character in enumerate(bible.characters, start=1)}
    location_index = {full.split("：", 1)[0].strip(): index for index, full in enumerate(bible.locations, start=1)}
    references: list[dict] = []
    bindings: list[str] = []
    count = 0
    # Two views per actor sharpen identity, but a crowded shot would then carry
    # ten reference images and the model starts blending faces.  Past two named
    # actors, give each one its turnaround only.
    two_views = len(cast) <= TWO_VIEW_CAST_LIMIT
    for name in cast:
        asset = f"character_{character_index[name]:03d}"
        count += 1
        first = count
        references.append({"tag": f"@图片{first}", "role": "character", "name": name, "asset_id": asset, "path": f"series_assets/characters/{asset}/turnaround.jpeg"})
        if two_views:
            count += 1
            second = count
            references.append({"tag": f"@图片{second}", "role": "character", "name": name, "asset_id": asset, "path": f"series_assets/characters/{asset}/expressions.jpeg"})
            bindings.append(f"<{name}>对应@图片{first}和@图片{second}，只采用五官、发型、体型和服装，不采用图片背景、姿势和构图")
        else:
            bindings.append(f"<{name}>只对应@图片{first}，只采用五官、发型、体型和服装，不采用图片背景、姿势和构图；不得把该角色的长相用在其他人身上")
    full = location_map[location_short]
    location_asset = f"location_{location_index[location_short]:03d}"
    count += 1
    references.append({"tag": f"@图片{count}", "role": "location", "name": location_short, "asset_id": location_asset, "path": f"series_assets/locations/{location_asset}/establishing.jpeg"})
    description = compact(full.split("：", 1)[1] if "：" in full else full, 40)
    location_binding = f"@图片{count}用于<{location_short}>的建筑、地面、固定道具和光线（{description}），不采用图中人物"
    return references, bindings, location_binding


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
            f"声音：{sound_clause(shot)}。结束时：{compact(shot['end_state'])}。"
        )
    scales = "、".join(dict.fromkeys(shot["shot_scale"] for shot in shots))
    ambience = list(dict.fromkeys(shot["sfx"] for shot in shots if shot.get("sfx")))
    ambience_text = "、".join(ambience) if ambience else "现场环境声"
    lines.append(f"画面呈现{(grammar or {}).get('style_line') or bible.visual_style}。")
    lines.append(f"镜头采用{frame['text']}，按阶段切换景别（{scales}），每个阶段开始时切一次画面，阶段内机位固定不运镜；{frame['composition']}；同一场景内保持人物左右位置和视线方向不变。")
    lines.append(f"声音包括角色对白、<{ambience_text}>和与动作同步的音效；无背景音乐；不要字幕。")
    lines.append("【保持一致】保持人物身份、数量、服装、固定道具位置、空间方向和声音关系稳定；画面中不出现任何文字、数字、字幕、Logo或水印；不出现血液和伤口；不新增具名人物。")
    avoid = [compact(shot.get("avoid", "")) for shot in shots if shot.get("avoid")]
    avoid = list(dict.fromkeys(a for a in avoid if a))
    rejects = [str(r) for r in (grammar or {}).get("rejects", []) if r]
    if avoid or rejects:
        lines.append("【不要】" + "；".join([*avoid, *rejects]) + "。")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--bible", type=Path, required=True)
    parser.add_argument("--grammar", type=Path, help="visual_grammar.json; defaults to <novel dir>/visual_grammar.json when present")
    parser.add_argument("--style", choices=("2d", "3d"), help="override profile.json style")
    parser.add_argument("--frame", choices=("9:16", "16:9"), help="override profile.json frame")
    args = parser.parse_args()
    episode_dir = args.episode_dir.resolve()
    script = json.loads((episode_dir / "chapter_script.json").read_text(encoding="utf-8"))
    bible = StoryBible.model_validate_json(args.bible.read_text(encoding="utf-8"))
    location_map = {full.split("：", 1)[0].strip(): full for full in bible.locations}
    grammar = load_grammar(args.grammar, episode_dir)
    profile = load_profile(episode_dir.parent, style=args.style, frame=args.frame)
    frame = frame_spec(profile)
    overrides_path = episode_dir / "clip_overrides.json"
    overrides = json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.is_file() else {}
    shots = script["shots"]
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
    clips_raw = pack(shots)
    clips: list[dict] = []
    for number, clip in enumerate(clips_raw, start=1):
        clip_id = f"clip_{number:02d}"
        shot_indexes = [shot["index"] for shot in clip["shots"]]
        if clip["kind"] == "title_card":
            clips.append({
                "clip_id": clip_id,
                "kind": "title_card",
                "shot_indexes": shot_indexes,
                "seconds_estimate": clip["seconds"],
                "request_seconds": 3,
                "text": "\n".join(turn["text"] for turn in clip["shots"][0]["turns"]),
            })
            continue
        clip["request_seconds"] = int(min(MAX_CLIP_SECONDS, max(4, math.ceil(clip["seconds"]))))
        cast = clip_cast(clip)
        override = overrides.get(clip_id, {})
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
        references, bindings, location_binding = build_references(cast, clip["location"], bible, location_map)
        prompt = compile_prompt(clip, bible, cast, bindings, location_binding, grammar, frame)
        lint = {shot["index"]: lint_stage(shot) for shot in clip["shots"]}
        lint = {k: v for k, v in lint.items() if v}
        lines = [
            {"speaker_name": turn["speaker_name"], "delivery_mode": turn["delivery_mode"], "text": turn["text"]}
            for shot in clip["shots"]
            for turn in merged_turns(shot)
            if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"}
        ]
        clips.append({
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
            "spoken_text": "".join(line["text"] for line in lines),
            "prompt": prompt,
            "prompt_chars": len(prompt),
            "lint": lint,
            "override": override or None,
            "background_only": clip.get("background_only", []),
        })
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
        "visual_grammar": (grammar or {}).get("name"),
        "profile": profile,
    }
    plan = {"policy": POLICY, "limits": {"max_clip_seconds": MAX_CLIP_SECONDS, "soft_cut_seconds": SOFT_CUT_SECONDS, "max_stages": MAX_STAGES}, "totals": totals, "clips": clips}
    totals["lint_by_code"] = {k: v for k, v in totals["lint_by_code"].items() if v}
    atomic_write_json(episode_dir / "clip_plan.json", plan)
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
