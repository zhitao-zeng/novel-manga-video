#!/usr/bin/env python
"""Split the over-long stages of clip plans packed before the packer did it, leaving every other clip as it is.

Until 2026-09-11 the packer cut only between stages, so a stage longer than a clip became one clip whose request
was clamped to the cap: 星海 484 clip_05, 71 s of lines asked of a 15 s clip, 45 % of them never spoken.  Packing
such a chapter again from its script would also re-word every other clip - the packer's prompts have changed since
- and pay for all of them again.  This replaces only the clamped clips, each by the parts build_clip_plan_thin now
cuts it into (split_long_shot), keeps every other clip entry as it is, numbers the clips again in order, and moves
the rendered clips, director corrections and overrides to their new ids, so the next render generates only the new
parts.  A split clip's old video goes to work/clips_before_split/, its correction (it described the clamped clip)
to split_long_stages.json.

    split_long_stages.py outputs/<novel> [--chapters 1-50,60] [--margin 5] [--tier fast] [--apply]
    split_long_stages.py outputs/<novel> --rebuild-parts [--apply]

Without --apply it reports what it would change.  An episode another process is rendering is skipped.  The parts
are packed for --tier (fast, as the conductor plans): the first run on 2026-09-11 took profile.json's tier, which
for 星海 and 雾月 is quality, so their parts asked for expression cards the fast tier never builds and every render
stopped at "reference image missing"; --rebuild-parts builds the parts of episodes split earlier again.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import build_clip_plan_thin as packer  # noqa: E402
from novel_manga.util import atomic_write_json  # noqa: E402
from thin_batch import parse_chapters, pid_alive  # noqa: E402

MARGIN_SECONDS = 5.0  # a stage this much longer than its clip's request is split; a second or two over is left alone


def is_target(clip: dict, margin: float) -> bool:
    return (clip.get("kind") == "video" and len(clip.get("shot_indexes") or []) == 1
            and float(clip.get("seconds_estimate") or 0) > float(clip.get("request_seconds") or 0) + margin)


def resplit(plan: dict, shots_by_index: dict, build_entry, margin: float = MARGIN_SECONDS) -> tuple[list[dict], dict, dict]:
    """The plan's clips with every target replaced by its parts, numbered again in order.

    Returns (clips, {old id: new id} for the clips kept as they were, {old id: [part ids]} for the split ones);
    build_entry(raw clip, new id, old id) makes a part's plan entry."""
    clips: list[dict] = []
    moved: dict[str, str] = {}
    split: dict[str, list[str]] = {}
    for clip in plan["clips"]:
        parts = []
        if is_target(clip, margin) and clip["shot_indexes"][0] in shots_by_index:
            parts = packer.split_long_shot(copy.deepcopy(shots_by_index[clip["shot_indexes"][0]]))
        if len(parts) > 1:
            split[clip["clip_id"]] = []
            for part in parts:
                new_id = f"clip_{len(clips) + 1:02d}"
                raw = {"kind": "video", "location": part["location"], "shots": [part], "seconds": round(packer.shot_seconds(part), 2)}
                clips.append(build_entry(raw, new_id, clip["clip_id"]))
                split[clip["clip_id"]].append(new_id)
        else:
            new_id = f"clip_{len(clips) + 1:02d}"
            moved[clip["clip_id"]] = new_id
            clips.append({**clip, "clip_id": new_id})
    return clips, moved, split


def repoint_records(clip_dir: Path) -> None:
    """A take's asr.json names its clip id and video path: after the move both must be the new ones, or whatever reads
    the record finds the old path - by then another clip's take, or nothing."""
    for record in clip_dir.glob("attempt_*/*asr.json"):
        data = json.loads(record.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("video"):
            atomic_write_json(record, {**data, "clip_id": clip_dir.name, "video": str(record.parent / Path(data["video"]).name)})


def rename_clip_dirs(episode_dir: Path, moved: dict, split: dict) -> None:
    """Move each rendered clip to its new id - through temporary names, since the ids shift into each other - and
    set aside the old clip of every split stage, with any clip directory the plan did not name."""
    clips_dir = episode_dir / "work" / "clips"
    if not clips_dir.is_dir():
        return
    aside = episode_dir / "work" / "clips_before_split" / time.strftime("%Y%m%d-%H%M%S")
    staged = []
    for directory in sorted(p for p in clips_dir.iterdir() if p.is_dir() and p.name.startswith("clip_")):
        temporary = clips_dir / f".moving-{directory.name}"
        directory.rename(temporary)
        staged.append((directory.name, temporary))
    for old, temporary in staged:
        if old in moved:
            temporary.rename(clips_dir / moved[old])
            repoint_records(clips_dir / moved[old])
        else:
            aside.mkdir(parents=True, exist_ok=True)
            temporary.rename(aside / old)


def remap_json(path: Path, moved: dict, split: dict, to_parts: bool) -> dict:
    """Re-key a {clip id: value} file to the new ids.  A split clip's value goes to each of its parts when
    `to_parts`, else it is dropped; so is a key for a clip the plan did not have.  Returns what was dropped."""
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    kept, dropped = {}, {}
    for key, value in data.items():
        if key in moved:
            kept[moved[key]] = value
        elif key in split and to_parts:
            for part in split[key]:
                kept[part] = value
        else:
            dropped[key] = value
    atomic_write_json(path, kept)
    return dropped


def split_episode(episode_dir: Path, margin: float, apply: bool, tier: str | None = "fast") -> dict | None:
    plan_path = episode_dir / "clip_plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not any(is_target(clip, margin) for clip in plan.get("clips", [])):
        return None
    if (episode_dir / "thin_media_report.h3zh.json").is_file():
        return {"skipped": "waiting for the H3 keep-check"}
    limits = plan.get("limits") or {}
    packer.MAX_CLIP_SECONDS = float(limits.get("max_clip_seconds") or packer.MAX_CLIP_SECONDS)
    packer.SOFT_CUT_SECONDS = float(limits.get("soft_cut_seconds") or packer.SOFT_CUT_SECONDS)
    packer.MAX_STAGES = int(limits.get("max_stages") or packer.MAX_STAGES)
    ctx = packer.load_context(episode_dir, episode_dir.parent / "story_bible.json", tier=tier)
    shots = packer.prepared_shots(json.loads((episode_dir / "chapter_script.json").read_text(encoding="utf-8")), episode_dir)
    clips, moved, split = resplit(
        plan, {shot["index"]: shot for shot in shots},
        lambda raw, new_id, old_id: packer.clip_entry(raw, new_id, ctx, override=ctx["overrides"].get(old_id, {})), margin)
    summary = {"split": split, "new_clips": sum(len(ids) for ids in split.values()),
               "final": (episode_dir / f"{episode_dir.name}.mp4").is_file(), "mode": int(packer.MAX_CLIP_SECONDS)}
    if not split or not apply:
        return summary
    lock = episode_dir / ".render.lock"
    if lock.is_file():
        try:
            pid = int(lock.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            pid = 0
        if pid and pid_alive(pid):
            return {**summary, "skipped": f"being rendered (pid {pid})"}
    lock.write_text(str(os.getpid()), encoding="utf-8")
    try:
        rename_clip_dirs(episode_dir, moved, split)
        dropped = remap_json(episode_dir / "review_feedback.json", moved, split, to_parts=False)
        remap_json(episode_dir / "clip_overrides.json", moved, split, to_parts=True)
        record = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "packer_version": packer.PACKER_VERSION, "tier": tier, "split": split, "renamed": moved}
        atomic_write_json(plan_path, {**plan, "clips": clips, "totals": packer.plan_totals(clips, shots, ctx), "split_long_stages": record})
        atomic_write_json(episode_dir / "split_long_stages.json", {**record, "dropped_corrections": dropped})
    finally:
        lock.unlink(missing_ok=True)
    return summary


def locked(episode_dir: Path) -> int:
    try:
        pid = int((episode_dir / ".render.lock").read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0
    return pid if pid and pid_alive(pid) else 0


def rebuild_parts(episode_dir: Path, tier: str | None, apply: bool) -> dict | None:
    """Build again, from the script, the parts of an episode split earlier - with `tier`; ids and everything else
    in the plan stay as they are."""
    plan_path = episode_dir / "clip_plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    record = plan.get("split_long_stages")
    if not record:
        return None
    limits = plan.get("limits") or {}
    packer.MAX_CLIP_SECONDS = float(limits.get("max_clip_seconds") or packer.MAX_CLIP_SECONDS)
    packer.SOFT_CUT_SECONDS = float(limits.get("soft_cut_seconds") or packer.SOFT_CUT_SECONDS)
    packer.MAX_STAGES = int(limits.get("max_stages") or packer.MAX_STAGES)
    ctx = packer.load_context(episode_dir, episode_dir.parent / "story_bible.json", tier=tier)
    shots = packer.prepared_shots(json.loads((episode_dir / "chapter_script.json").read_text(encoding="utf-8")), episode_dir)
    by_index = {shot["index"]: shot for shot in shots}
    position = {clip["clip_id"]: n for n, clip in enumerate(plan["clips"])}
    clips = list(plan["clips"])
    for old_id, part_ids in record["split"].items():
        parts = packer.split_long_shot(copy.deepcopy(by_index[clips[position[part_ids[0]]]["shot_indexes"][0]]))
        if len(parts) != len(part_ids):
            return {"skipped": f"{old_id} splits into {len(parts)} parts now, not {len(part_ids)}"}
        for part_id, part in zip(part_ids, parts):
            raw = {"kind": "video", "location": part["location"], "shots": [part], "seconds": round(packer.shot_seconds(part), 2)}
            clips[position[part_id]] = packer.clip_entry(raw, part_id, ctx, override=ctx["overrides"].get(part_id, {}))
    summary = {"rebuilt": sum(len(ids) for ids in record["split"].values())}
    if not apply:
        return summary
    pid = locked(episode_dir)
    if pid:
        return {**summary, "skipped": f"being rendered (pid {pid})"}
    atomic_write_json(plan_path, {**plan, "clips": clips, "totals": packer.plan_totals(clips, shots, ctx),
                                  "split_long_stages": {**record, "tier": tier, "parts_rebuilt_at": time.strftime("%Y-%m-%d %H:%M:%S")}})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("novel_dir", type=Path)
    parser.add_argument("--chapters", help='only these episodes, e.g. "484" or "1-500,812"')
    parser.add_argument("--margin", type=float, default=MARGIN_SECONDS, help="split a single-stage clip estimated this many seconds over its request")
    parser.add_argument("--tier", choices=("fast", "quality"), default="fast", help="the tier the plans were packed for (the conductor plans fast)")
    parser.add_argument("--rebuild-parts", action="store_true", help="build again the parts of episodes split earlier (same ids)")
    parser.add_argument("--apply", action="store_true", help="write the plans (default: report only)")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    wanted = set(parse_chapters(args.chapters)) if args.chapters else None
    episodes = sorted((d for d in novel_dir.glob(f"{novel_dir.name}_*") if d.is_dir() and d.name.rsplit("_", 1)[-1].isdigit()),
                      key=lambda d: int(d.name.rsplit("_", 1)[1]))
    results = {}
    for episode in episodes:
        number = int(episode.name.rsplit("_", 1)[1])
        if wanted is not None and number not in wanted:
            continue
        if args.rebuild_parts:
            result = rebuild_parts(episode, args.tier, args.apply)
            if result:
                results[number] = result
                if result.get("skipped"):
                    print(f"  {number}: skipped ({result['skipped']})", flush=True)
            continue
        result = split_episode(episode, args.margin, args.apply, args.tier)
        if result:
            results[number] = result
            if result.get("skipped"):
                print(f"  {number}: skipped ({result['skipped']})", flush=True)
    if args.rebuild_parts:
        print(json.dumps({"episodes": len([r for r in results.values() if not r.get("skipped")]),
                          "parts": sum(r["rebuilt"] for r in results.values() if not r.get("skipped")),
                          "skipped": {n: r["skipped"] for n, r in results.items() if r.get("skipped")}, "applied": args.apply},
                         ensure_ascii=False, indent=1))
        return 0
    done = {n: r for n, r in results.items() if not r.get("skipped")}
    by_mode: dict[str, list[int]] = {}
    for n, r in done.items():
        if r.get("final"):
            by_mode.setdefault(str(r["mode"]), []).append(n)
    summary = {"episodes": len(done), "split_clips": sum(len(r["split"]) for r in done.values()),
               "new_clips": sum(r["new_clips"] for r in done.values()),
               "new_clips_in_finals": sum(r["new_clips"] for r in done.values() if r.get("final")),
               "finals_by_mode": {mode: ",".join(map(str, eps)) for mode, eps in by_mode.items()},
               "skipped": {n: r["skipped"] for n, r in results.items() if r.get("skipped")}, "applied": args.apply}
    if args.apply:
        atomic_write_json(novel_dir / "split_long_stages_report.json", {"summary": summary, "episodes": results})
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
