#!/usr/bin/env python3
"""Recover dialogue ranges reexpanded by an old rebuild, keeping existing clip IDs.

Called before rendering by thin_batch. No model calls, new cuts, or cast completion.
Only legacy entries with repeated source indexes or oversized shared stages are candidates; parts that cannot
be recovered from the whole plan are left alone and reported.
"""
from __future__ import annotations
from novel_manga.story.compilation import ClipCompiler
from novel_manga.application.packing.context import compiler_options
import novel_manga.story.compilation as compilation
import novel_manga.application.packing.context as packing_context
import novel_manga.application.packing.service as packing_service

import copy
from collections import Counter
import json
from pathlib import Path
import threading

from novel_manga.util import atomic_write_json

LOCK = threading.Lock()  # retain the existing serialized recovery operation


def candidates(plan: dict) -> set[str]:
    occurrences = Counter(i for c in plan.get("clips", []) if c.get("kind") == "video"
                          for i in set(c.get("shot_indexes", [])))
    return {c["clip_id"] for c in plan.get("clips", []) if c.get("kind") == "video" and not c.get("shot_parts")
            and (len(c.get("shot_indexes", [])) > len(set(c.get("shot_indexes", [])))
                 or (c.get("seconds_estimate", 0) > c.get("request_seconds", 0)
                     and any(occurrences[i] > 1 for i in c.get("shot_indexes", []))))}


def recover(episode_dir: Path, plan: dict, script: dict) -> tuple[dict, list[str], dict[str, str]]:
    targets = candidates(plan)
    if not targets:
        return plan, [], {}
    with LOCK:
        ctx = packing_context.context_for_plan(episode_dir, episode_dir.parent / "story_bible.json", plan)
        shots = packing_service.prepared_shots(copy.deepcopy(script), episode_dir)
        clips, changed, skipped = [], [], {}
        for before in plan["clips"]:
            cid = before["clip_id"]
            after = before
            if cid in targets:
                try:
                    pieces = ClipCompiler(ctx.get('compiler_options') or compiler_options()).shots_for_plan(plan, shots, {cid})[cid]
                    if not any(p.get("split_part", [1, 1])[1] > 1 for p in pieces):
                        raise ValueError("no recoverable sibling ranges")
                    seconds = round(sum(ClipCompiler(ctx.get('compiler_options') or compiler_options()).shot_seconds(p) for p in pieces), 2)
                    if seconds > ctx["compiler_options"].max_clip_seconds:
                        raise ValueError("recovered range still exceeds clip limit")
                    raw = {"kind": "video", "location": before.get("location") or pieces[0]["location"],
                           "shots": pieces, "seconds": seconds}
                    after = packing_service.clip_entry(raw, cid, ctx)
                    changed.append(cid)
                except ValueError as error:
                    skipped[cid] = str(error)
            clips.append(after)
        if not changed:
            return plan, [], skipped
        return {**plan, "clips": clips, "totals": compilation.plan_totals(clips, shots, ctx)}, changed, skipped


def repair_episode(episode_dir: Path, *, apply: bool = False) -> dict:
    """Caller owns this episode (batch renderer or paused manager maintenance)."""
    path = episode_dir / "clip_plan.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not candidates(plan):
        return {"changed": [], "skipped": {}}
    script = json.loads((episode_dir / "chapter_script.json").read_text(encoding="utf-8"))
    updated, changed, skipped = recover(episode_dir, plan, script)
    if apply and changed:
        backup = episode_dir / "clip_plan.json.bak-split-ranges"
        if not backup.exists():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        atomic_write_json(path, updated)
    return {"changed": changed, "skipped": skipped}
