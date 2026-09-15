"""The render-run count of an episode, and what its files say about it - one reading for thin_batch and the conductor.

thin_batch.py counts render runs per episode and stops retrying one after RENDER_RUNS_PER_PLAN, so a
clip that fails the same way every time is not regenerated (and paid for) on every round.  The count
is kept for what the plan and its corrections say: a re-plan or a new correction starts it again, a
file written again with the same content does not (the mtimes are only the quick check).  The conductor reads the same count to tell an episode to leave alone from one still worth
a lane.  Both go through this module: the conductor once compared the plan's mtime alone with the
pair thin_batch writes, read 0 runs everywhere, and kept given-up episodes renderable.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from thin_profile import h3_prompt_fingerprint, plan_fingerprint

RENDER_RUNS_PER_PLAN = 3
REVIEW_POLICY = "thin-review-v1.17-story"
RUNS_FILE = ".render_runs"


def runs_key(directory: Path) -> list[float]:
    plan, feedback = directory / "clip_plan.json", directory / "review_feedback.json"
    return [plan.stat().st_mtime if plan.is_file() else 0.0, feedback.stat().st_mtime if feedback.is_file() else 0.0]


def content_key(directory: Path) -> str:
    """What the runs are counted for: the plan's renderable content and the corrections.  Keyed on the files' mtimes
    alone, a rewrite that changed neither (a re-pack whose clips came out the same, a rebuild of unchanged split
    parts, the H3 converter's write) started a given-up episode's count again, and a paid lane paid three more runs."""
    try:
        plan = json.loads((directory / "clip_plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    material = json.dumps([plan_fingerprint(plan), corrections(directory)], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def render_runs(directory: Path) -> int:
    """Render runs so far on the episode's current plan and corrections."""
    try:
        saved = json.loads((directory / RUNS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(saved, dict):
        return 0
    if saved.get("plan_mtime") != runs_key(directory):
        # Written since: the count stands if what the files say did not change.
        if not saved.get("content_key") or saved["content_key"] != content_key(directory):
            return 0
    return int(saved.get("runs", 0))


def count_run(directory: Path) -> int:
    runs = render_runs(directory) + 1
    record = {"plan_mtime": runs_key(directory), "content_key": content_key(directory), "runs": runs}
    temp = directory / f"{RUNS_FILE}.{os.getpid()}.tmp"
    temp.write_text(json.dumps(record), encoding="utf-8")
    os.replace(temp, directory / RUNS_FILE)
    return runs


def corrections(directory: Path) -> dict:
    """The episode's director corrections (review_feedback.json); {} when there are none."""
    try:
        data = json.loads((directory / "review_feedback.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def episode_status(directory: Path, h3_lane: bool) -> str:
    """What an episode's files say about it: no_plan; pending (no final yet, or the final is gone); stale (the plan, a
    correction or, on an H3 lane, an English prompt changed since the final was cut); clips_failed;
    done_with_warnings (a preview: clips failed the speech gate, or the media checks failed); done.

    thin_batch and the conductor both read this.  The conductor used to count any episode with a video file as
    done, so a re-planned or corrected episode in a range already rendered was never scheduled again."""
    plan_path, report_path = directory / "clip_plan.json", directory / "thin_media_report.json"
    if not plan_path.is_file():
        return "no_plan"
    from clip_readiness import current_blocks
    if current_blocks(directory):
        return "plan_blocked"
    if not report_path.is_file():
        return "pending"
    data = json.loads(report_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    stamped = data.get("clip_plan_fingerprint")
    if stamped and stamped != plan_fingerprint(plan):
        return "stale"  # reports from before the stamp are trusted; a re-plan deletes them anyway
    # Two inputs the plan fingerprint leaves out also change what a clip is asked for: a director correction
    # (appended to the prompts it names) and, on a local-H3 lane, the English prompt.
    if (data.get("review_feedback") or {}) != corrections(directory):
        return "stale"
    if h3_lane and data.get("prompt_h3_fingerprint") not in (None, h3_prompt_fingerprint(plan)):
        return "stale"
    if data.get("failed_clips") or not data.get("assembly"):
        return "clips_failed"
    assembly = data["assembly"]
    final = Path(assembly["final_video"]) if assembly.get("pending_publish") and assembly.get("final_video") else directory / f"{directory.name}.mp4"
    if not final.is_file():
        return "pending"
    from thin_profile import blocking_clip_failures, assembly_gate_passed
    if blocking_clip_failures(directory.parent,data,directory) or not assembly_gate_passed(directory.parent,assembly,directory):
        return "done_with_warnings"
    return "done"


def gate_failures(directory: Path) -> list[str]:
    """Clips of the episode's final that failed the speech gate: what a retake can fix.  A final that fails only a
    media check was made from clips that all passed - putting it together again changes nothing."""
    try:
        data = json.loads((directory / "thin_media_report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    from thin_profile import blocking_clip_failures
    return blocking_clip_failures(directory.parent,data,directory)
