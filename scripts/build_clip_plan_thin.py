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
import copy
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

from novel_manga.story.framing import blocking_note
from novel_manga.story.actions import normalize_actions
from novel_manga.story.dialogue import merged_turns, nonverbal_sound
from novel_manga.story.compilation import (CompilerOptions, ClipCompiler,
    ABSTRACT, CAMERA_MOVE, CLAUSE_END, EXECUTION_RULES, LIGHT_NOUNS, NO_SUBTITLES, ORDINALS, READABLE_TEXT, SENTENCE_END, STAGE_LABELS, STRIP_PUNCT, anchor_of, chat_turns, compact, is_title_card, lint_stage, plan_totals, spoken_chars, text_chunks)
from novel_manga.models import StoryBible
from plan_chapter_thin import ALIASES as PLAN_ALIASES, load_entity_index, mentioned_characters  # noqa: E402
from thin_phases import chapter_of, load_phases, phase_for, phase_labels, phased  # noqa: E402
from novel_manga.util import atomic_write_json

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thin_profile import frame_spec, is_fast, load_genre, load_profile, plan_fingerprint

POLICY = "thin-clip-plan-v12-six-stages" + ("-15s" if os.environ.get("NOVEL_CLIP_SECONDS_MAX", "").strip() in {"15", "15.0"} else "")
TWO_VIEW_CAST_LIMIT = 2
LEAD_ROLES = {"主角", "女主角", "男主角"}
# Seedance sometimes burns its own caption bar into the picture; the film has its own
# subtitle track, so every prompt forbids it explicitly.
GENRE_REJECTS: list[str] = []  # from the genre preset; appended to 【不要】
GENRE_CROWD = ""
# NOVEL_CLIP_SECONDS_MAX=15 is the sd2.0 lane (its reference-to-video mode
# stops at 15 s): shorter clips, three stages at most, an earlier soft cut.
MAX_CLIP_SECONDS = float(os.environ.get("NOVEL_CLIP_SECONDS_MAX", "30") or 30)
SOFT_CUT_SECONDS = 18.0 if MAX_CLIP_SECONDS > 15 else round(MAX_CLIP_SECONDS * 0.6, 1)
MAX_STAGES = 6 if MAX_CLIP_SECONDS > 15 else 3
DEFAULT_ANON_VOICE = {
    "无名测验员": "画外的中年测验员（男声）",
    "无名族人": "画外一名族人",
    "无名少年": "画外一名少年",
    "无名少女": "画外一名少女",
    "无名群声": "画外的人群",
}
ANON_VOICE = dict(DEFAULT_ANON_VOICE)


def compiler_options(frame=None):
    from plan_chapter_thin import ENTITY_FORMS, ENTITY_GENERIC
    return CompilerOptions(max_clip_seconds=MAX_CLIP_SECONDS, soft_cut_seconds=SOFT_CUT_SECONDS,
        max_stages=MAX_STAGES, pack_mode=PACK_MODE, min_standalone_seconds=MIN_STANDALONE_SECONDS,
        chat_screen=copy.deepcopy(CHAT_SCREEN), anon_voice=copy.deepcopy(ANON_VOICE),
        genre_rejects=list(GENRE_REJECTS), genre_crowd=GENRE_CROWD,
        entity_forms=copy.deepcopy(ENTITY_FORMS), entity_generic=copy.deepcopy(ENTITY_GENERIC),
        aliases=dict(PLAN_ALIASES), frame=frame or frame_spec({'frame':'9:16'}),
        voices=dict(VOICES), two_view_cast_limit=TWO_VIEW_CAST_LIMIT)






def shot_seconds(*args, settings=None, **kwargs):
    compiler = ClipCompiler(settings or compiler_options())
    result = compiler.shot_seconds(*args, **kwargs)
    return result




PACKER_VERSION = "thin-packer-2026-09-12+split-keeps-cast"
# "planned": every rule below cuts (today's behaviour).  "execution": only the rules the
# video service enforces cut - location, length cap, stage ceiling - and the planner's
# clip_hint and the source-segment boundary are recorded but not acted on.
PACK_MODE = os.environ.get("NOVEL_PACK_MODE", "execution").strip() or "execution"
DECISIONS: list[dict] = []  # observation only; written to pack_decisions.json by main()


def _cut_checks(*args, settings=None, **kwargs):
    compiler = ClipCompiler(settings or compiler_options())
    result = compiler._cut_checks(*args, **kwargs)
    return result






def split_long_shot(*args, settings=None, **kwargs):
    compiler = ClipCompiler(settings or compiler_options())
    result = compiler.split_long_shot(*args, **kwargs)
    DECISIONS.extend(compiler.decisions)
    return result


def pack(*args, settings=None, **kwargs):
    compiler = ClipCompiler(settings or compiler_options())
    result = compiler.pack(*args, **kwargs)
    DECISIONS[:] = compiler.decisions
    return result


MIN_STANDALONE_SECONDS = 8.0


def absorb_small_clips(*args, settings=None, **kwargs):
    compiler = ClipCompiler(settings or compiler_options())
    result = compiler.absorb_small_clips(*args, **kwargs)
    return result














def load_grammar(path: Path | None, episode_dir: Path) -> dict | None:
    candidate = path or (episode_dir.parent / "visual_grammar.json")
    if candidate and candidate.is_file():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return None




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


def screen_clause(*args, settings=None, **kwargs):
    compiler = ClipCompiler(settings or compiler_options())
    result = compiler.screen_clause(*args, **kwargs)
    return result


def sound_clause(*args, settings=None, **kwargs):
    compiler = ClipCompiler(settings or compiler_options())
    result = compiler.sound_clause(*args, **kwargs)
    return result


def clip_cast(clip, *, settings=None):
    cast, background = ClipCompiler(settings or compiler_options()).select_cast(clip)
    if background is not None:
        clip['background_only'] = background
    return cast




_BODIES_CACHE: dict = {}


def bodies_for(novel_dir, chapter) -> dict[str, tuple[str, str]]:
    """name -> (body's name, body's card) for cast members the entity ledger says act through someone else's body in
    this chapter (occupies_body / impersonates accepted for the book): the picture shows that body, so the clip
    references that card and describes that look.  Empty without a ledger or a card for the body."""
    key = (str(novel_dir), int(chapter or 0))
    if key in _BODIES_CACHE:
        return _BODIES_CACHE[key]
    out: dict[str, tuple[str, str]] = {}
    try:
        from entity_ledger_thin import snapshot
        sheet = snapshot(Path(novel_dir), int(chapter))
        raw = json.loads((Path(novel_dir) / "entity" / "entities.json").read_text(encoding="utf-8"))
        entities = raw if isinstance(raw, dict) else {e["id"]: e for e in raw}
        for row in sheet.get("cast", []):
            if row.get("acts_through_other_body") and row.get("card"):
                out[row["name"]] = (str(entities.get(row["body"], {}).get("canonical") or row["body"]), str(row["card"]))
    except Exception:  # noqa: BLE001 - no ledger, chapter not read, or an old ledger layout: nothing changes
        out = {}
    _BODIES_CACHE[key] = out
    return out


def build_references(cast: list[str], location_short: str, bible: StoryBible, location_map: dict[str, str], speakers: tuple[str, ...] = (), novel_dir: Path | None = None, chapter: int | None = None, *, settings=None) -> tuple[list[dict], list[str], str]:
    settings = settings or compiler_options()
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
    # Off unless asked for: the second view never proved itself and doubled the reference count; the fast
    # tier renders from the turnaround alone (NOVEL_TWO_VIEWS=1 restores the old behaviour for new plans).
    two_views = len(cast) <= settings.two_view_cast_limit and os.environ.get("NOVEL_TWO_VIEWS", "").strip() == "1"
    # A character with phases (series_assets/phases.json) references the card of the phase this chapter is in,
    # and the anchor describes that look - 沈玄川 is white-haired from ch1406, his base card is not.
    phases = load_phases(novel_dir) if novel_dir is not None else {}
    by_name = {character.name: character for character in bible.characters}
    bodies = bodies_for(novel_dir, chapter) if novel_dir is not None and chapter else {}
    for name in cast:
        phase = phase_for(phases, name, chapter)
        asset = str(phase["asset_id"]) if phase else f"character_{character_index[name]:03d}"
        look = phased(by_name[name], phase)
        host = bodies.get(name)
        body_note = ""
        if host:
            # 艾琳娜 in 薇奥拉's body is drawn as 薇奥拉: her card, her look, and the prompt says so
            asset = host[1]
            if host[0] in by_name:
                look = phased(by_name[host[0]], phase_for(phases, host[0], chapter))
            body_note = f"（此时在{host[0]}的身体里，外形完全是{host[0]}的样子）"
        changed = tuple(f for f in ("hair", "appearance", "silhouette", "palette") if phase and phase.get(f))
        count += 1
        first = count
        references.append({"tag": f"@图片{first}", "role": "character", "name": name, "asset_id": asset, "path": f"series_assets/characters/{asset}/turnaround.jpeg",
                           **({"phase": str(phase.get("label", ""))} if phase else {})})
        sheet = novel_dir is not None and (novel_dir / "series_assets" / "characters" / asset / "expressions.jpeg").is_file()
        lead_sheet = settings.two_view_cast_limit > 0 and sheet and name in leads and os.environ.get("NOVEL_TWO_VIEWS", "").strip() == "1"
        # Only a sheet that exists is referenced: the fast tier never draws one at render time (the full tier
        # did, which is what the old "phase is None" clause assumed), and a plan that promises a missing file
        # fails the pre-render check for the whole episode.  Without a novel_dir to look at, keep the old rule.
        wants_sheet = sheet if novel_dir is not None else phase is None
        if (two_views and wants_sheet) or lead_sheet:
            count += 1
            second = count
            references.append({"tag": f"@图片{second}", "role": "character", "name": name, "asset_id": asset, "path": f"series_assets/characters/{asset}/expressions.jpeg"})
            anchor = anchor_of(name, bible, character=look, prefer=changed)
            bindings.append(
                f"<{name}>{body_note}对应@图片{first}和@图片{second}：@图片{first}定五官、发型、年龄感和肤色，"
                f"@图片{second}定身体比例、服装版型、主色和配饰；两张都不采用背景、姿势和构图；"
                # The single-view binding always said this; the two-view one - every lead - did not, and 408 of
                # 雾月's 574 doppelganger clips show an extra person wearing the lead's face.
                "画面中其他任何人都不得使用这两张图的相貌、发型或服装，该角色只能出现一次"
                + (f"。{name}的辨识特征：{anchor}" if anchor else ""))
        else:
            anchor = anchor_of(name, bible, character=look, prefer=changed)
            bindings.append(
                f"<{name}>{body_note}只对应@图片{first}，只采用五官、发型、体型和服装，不采用图片背景、姿势和构图；"
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
    for voice_index, name in enumerate((n for n in cast if n in speakers and n in settings.voices), start=1):
        references.append({"tag": f"@音频{voice_index}", "role": "voice", "name": name, "path": settings.voices[name]})
    description = compact(full.split("：", 1)[1] if "：" in full else full, 40)
    location_binding = f"@图片{count}用于<{location_short}>的建筑、地面、固定道具和光线（{description}），不采用图中人物"
    return references, bindings, location_binding




def compile_prompt(*args, settings=None, **kwargs):
    compiler = ClipCompiler(settings or compiler_options())
    result = compiler.compile_prompt(*args, **kwargs)
    return result


def load_context(episode_dir: Path, bible_path: Path, grammar_path: Path | None = None, style: str | None = None,
                 frame: str | None = None, tier: str | None = None) -> dict:
    """Everything an episode's clip entries are built from besides the shots - and the module settings the packer
    reads (genre rejects, crowd line, anonymous voices, chat screen, voice bank, cards per character)."""
    global GENRE_REJECTS, GENRE_CROWD, TWO_VIEW_CAST_LIMIT
    bible = StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
    grammar = load_grammar(grammar_path, episode_dir)
    load_chat_screen(episode_dir.parent)
    load_voices(episode_dir.parent)
    load_entity_index(episode_dir.parent, chapter_of(episode_dir))
    profile = load_profile(episode_dir.parent, style=style, frame=frame, tier=tier)
    genre = load_genre(profile)
    GENRE_REJECTS = [x for x in [genre.get("era_rejects", "")] + list(genre.get("grammar_rejects_extra", [])) if x]
    GENRE_CROWD = genre.get("crowd_default", "")
    ANON_VOICE.clear()
    ANON_VOICE.update(DEFAULT_ANON_VOICE)
    ANON_VOICE.update(genre.get("anon_voice") or {})
    TWO_VIEW_CAST_LIMIT = 0 if is_fast(profile) else 2
    overrides_path = episode_dir / "clip_overrides.json"
    return {
        "episode_dir": episode_dir, "bible": bible, "grammar": grammar, "profile": profile, "frame": frame_spec(profile),
        "compiler_options": compiler_options(frame_spec(profile)),
        "location_map": {full.split("：", 1)[0].strip(): full for full in bible.locations},
        "overrides": json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.is_file() else {},
    }


def prepared_shots(script: dict, episode_dir: Path) -> list[dict]:
    from scene_context_thin import prepare_scene
    return prepare_scene(script, episode_dir).shots


def context_for_plan(episode_dir: Path, bible_path: Path, plan: dict) -> dict:
    """Rebuild with the plan's recorded frame, style, tier and clip limits, not today's profile defaults."""
    global MAX_CLIP_SECONDS, MAX_STAGES, SOFT_CUT_SECONDS
    profile = (plan.get("totals") or {}).get("profile") or {}
    limits = plan.get("limits") or {}
    MAX_CLIP_SECONDS = float(limits.get("max_clip_seconds") or (15 if "-15s" in plan.get("policy", "") else 30))
    MAX_STAGES = int(limits.get("max_stages") or (3 if MAX_CLIP_SECONDS <= 15 else 6))
    SOFT_CUT_SECONDS = float(limits.get("soft_cut_seconds") or (MAX_CLIP_SECONDS * 0.6))
    return load_context(episode_dir, bible_path, style=profile.get("style"), frame=profile.get("frame"), tier=profile.get("tier"))


def shots_for_plan(*args, settings=None, **kwargs):
    compiler = ClipCompiler(settings or compiler_options())
    result = compiler.shots_for_plan(*args, **kwargs)
    return result


def clip_entry(clip: dict, clip_id: str, ctx: dict, override: dict | None = None) -> dict:
    """The plan entry of one packed clip (a title card or a video clip); `override` defaults to the episode's
    clip_overrides.json entry for `clip_id`."""
    clip = copy.deepcopy(clip)
    options = ctx.get("compiler_options") or compiler_options(ctx.get("frame"))
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
    clip["request_seconds"] = int(min(options.max_clip_seconds, max(4, math.ceil(clip["seconds"]))))
    cast = clip_cast(clip, settings=options)
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
    references, bindings, location_binding = build_references(cast, clip["location"], bible, ctx["location_map"], speakers=speakers, novel_dir=ctx["episode_dir"].parent, chapter=chapter_of(ctx["episode_dir"]), settings=options)
    prompt = compile_prompt(clip, bible, cast, bindings, location_binding, ctx["grammar"], ctx["frame"], settings=options)
    lint = {shot["index"]: lint_stage(shot) for shot in clip["shots"]}
    lint = {k: v for k, v in lint.items() if v}
    lines = [
        {"speaker_name": turn["speaker_name"], "delivery_mode": turn["delivery_mode"], "text": turn["text"]}
        for shot in clip["shots"]
        for turn in merged_turns(shot)
        if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"}
    ]
    from dialogue_binding import clip_bindings
    entry = {
        "clip_id": clip_id,
        "kind": "video",
        "location": clip["location"],
        "shot_indexes": shot_indexes,
        "segment_ids": list(dict.fromkeys(shot["segment_id"] for shot in clip["shots"])),
        "shot_parts": [{"index": shot["index"], "part": list(shot.get("split_part") or (1, 1))} for shot in clip["shots"]],
        "stage_count": len(clip["shots"]),
        "seconds_estimate": clip["seconds"],
        "request_seconds": clip["request_seconds"],
        "cast": cast,
        "references": references,
        "lines": lines,
        "dialogue_bindings": clip_bindings(clip['shots']),
        "chat_lines": [{"speaker_name": t["speaker_name"], "text": t["text"].strip(), "chat_target": str(t.get("chat_target") or "").strip()} for shot in clip["shots"] for t in chat_turns(shot)],
        "spoken_text": "".join(line["text"] for line in lines),
        "prompt": prompt,
        "prompt_chars": len(prompt),
        "lint": lint,
        "override": override or None,
        "background_only": clip.get("background_only", []),
        # what the stages said about who is a listener (back to camera / off frame) and which unnamed extras are in
        # frame: the entry keeps no shots, and the judge reads these from here
        "listeners": list(dict.fromkeys(l for shot in clip["shots"] for l in (shot.get("listeners") or []))),
        "extras": list(dict.fromkeys(e for shot in clip["shots"] for e in (shot.get("extras") or []))),
    }
    from h3_request_checks import source_crowds
    from story_identity import current_context, read
    segments = read(ctx['episode_dir'] / 'segments.json', [])
    passage = '\n'.join(s['text'] for s in segments if s['segment_id'] in entry['segment_ids'])
    crowds = source_crowds(entry, bible.model_dump(), passage, context=current_context(ctx['episode_dir']))
    if crowds:
        entry['crowd_roles'] = crowds
    return entry




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
    clips = [clip_entry(clip, f"clip_{number:02d}", ctx) for number, clip in enumerate(pack(shots, settings=ctx["compiler_options"]), start=1)]
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
