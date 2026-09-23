"""Clip plan construction with explicit per-episode inputs; never publishes artifacts."""
from __future__ import annotations

import copy
import math
from pathlib import Path
from novel_manga.story.compilation import ClipCompiler, plan_totals, lint_stage, chat_turns
from novel_manga.story.dialogue import merged_turns
from novel_manga.application.packing.context import compiler_options, POLICY, PACKER_VERSION
from novel_manga.application.packing.assets import build_references, bodies_for
from novel_manga.application.identity.store import load_chapter
from novel_manga.application.identity.phases import chapter_of, phase_labels


def clip_cast(clip, *, settings=None):
    cast, background = ClipCompiler(settings or compiler_options()).select_cast(clip)
    if background is not None:
        clip['background_only'] = background
    return cast


def prepared_shots(script: dict, episode_dir: Path, *, identity_data=None) -> list[dict]:
    from novel_manga.planning.storyboard import require_bound_storyboard
    require_bound_storyboard(script)
    from novel_manga.application.identity.scene import prepare_scene
    return prepare_scene(script, episode_dir, identity_data=identity_data).shots


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
    clip_prop_names = list(dict.fromkeys(
        prop for shot in clip["shots"] for prop in (shot.get("props") or [])))
    # Shot-level wearing state: a stage may put a wearable on, take it off, or fly an empty suit in.
    # Every stage of the clip gets a say; the last word wins per name, and a clip whose stages
    # disagree takes the union of what anyone wears (a prop card rides or it does not - half-wearing
    # a suit into a request is the one option that draws a wrong picture).
    worn_overrides: dict[str, str | None] = {}
    for shot in clip["shots"]:
        for name, wears in (shot.get("wears") or {}).items():
            worn_overrides[name] = str(wears) if wears else None
    references, bindings, location_binding = build_references(cast, clip["location"], bible, ctx["location_map"], speakers=speakers, novel_dir=ctx["episode_dir"].parent, chapter=chapter_of(ctx["episode_dir"]), settings=options, identity_data=ctx.get("identity_data"), body_refs=ctx.get("body_refs"), props=clip_prop_names or None, props_index={p.name: p for p in getattr(bible, "props", None) or []}, worn_overrides=worn_overrides or None)
    if any(s.get('scene_id') for s in clip['shots']):
        location_binding = location_binding.replace('、固定道具和光线', '和地形；时间、光线和可移动道具以本场逐镜描述为准')
    prompt = ClipCompiler(options).compile_prompt(clip, bible, cast, bindings, location_binding, ctx["grammar"], ctx["frame"])
    lint = {shot["index"]: lint_stage(shot, camera_policy=options.camera_policy) for shot in clip["shots"]}
    lint = {k: v for k, v in lint.items() if v}
    lines = [
        {"speaker_name": turn["speaker_name"], "delivery_mode": turn["delivery_mode"], "text": turn["text"]}
        for shot in clip["shots"]
        for turn in merged_turns(shot)
        if turn["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"}
    ]
    from novel_manga.application.identity.dialogue import clip_bindings
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
    if any(s.get('scene_id') for s in clip['shots']):
        entry.update(scene_ids=list(dict.fromkeys(s['scene_id'] for s in clip['shots'])),
                     shot_ids=[s['shot_id'] for s in clip['shots']],
                     shot_sound=['；'.join(filter(None, [s.get('sfx', ''),
                         *[t['speaker_name'] + '：' + t['text'] for t in s['turns'] if t['delivery_mode'] == 'singing']]))
                         for s in clip['shots']],
                     shot_timing=[{'seconds': s['duration_seconds'], 'cut': s.get('cut', '')} for s in clip['shots']],
                     scene_time=clip['shots'][0].get('scene_time', ''),
                     render_family=ctx.get('render_family') or '')
        entry['segment_ids'] = list(dict.fromkeys(r['segment_id'] for s in clip['shots'] for r in s.get('source_refs', [])))
    from novel_manga.story.h3 import source_crowds
    data = ctx.get('identity_data')
    data = data if data is not None else load_chapter(ctx['episode_dir'])
    segments = data.segments
    passage = '\n'.join(s['text'] for s in segments if s['segment_id'] in entry['segment_ids'])
    crowds = source_crowds(entry, bible.model_dump(), passage, context=data.context)
    if crowds:
        entry['crowd_roles'] = crowds
    return entry


def compile_plan(script: dict, context: dict) -> tuple[dict, dict]:
    """Build the plan and its cut explanation without sharing diagnostic state."""
    shots = prepared_shots(script, context['episode_dir'], identity_data=context.get('identity_data'))
    compiler = ClipCompiler(context['compiler_options'])
    packed = compiler.pack(shots)
    chapter = chapter_of(context["episode_dir"])
    if chapter and any(c["kind"] == "video" for c in packed):
        context = {**context, "body_refs": bodies_for(context["episode_dir"].parent, chapter)}
    clips = [clip_entry(clip, f'clip_{number:02d}', context)
             for number, clip in enumerate(packed, start=1)]
    options = compiler.options
    limits = {'max_clip_seconds': options.max_clip_seconds, 'soft_cut_seconds': options.soft_cut_seconds,
              'max_stages': options.max_stages}
    if options.camera_policy != 'fixed':
        limits['camera_policy'] = options.camera_policy
    plan = {'policy': POLICY, 'phases': phase_labels(clips), 'limits': limits,
            'totals': plan_totals(clips, shots, context), 'clips': clips}
    decisions = {'packer_version': PACKER_VERSION, 'pack_mode': options.pack_mode,
                 'limits': {**limits, 'min_standalone_seconds': options.min_standalone_seconds},
                 'decisions': copy.deepcopy(compiler.decisions)}
    return plan, decisions
