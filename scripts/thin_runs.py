"""The render-run count an episode has on its current clip plan and director corrections.

thin_batch.py counts render runs per episode and stops retrying one after RENDER_RUNS_PER_PLAN, so a
clip that fails the same way every time is not regenerated (and paid for) on every round.  The count
is kept per [clip_plan.json mtime, review_feedback.json mtime]: a re-plan or a new correction starts
it again.  The conductor reads the same count to tell an episode to leave alone from one still worth
a lane.  Both go through this module: the conductor once compared the plan's mtime alone with the
pair thin_batch writes, read 0 runs everywhere, and kept given-up episodes renderable.
"""
from __future__ import annotations

import json
from pathlib import Path

RENDER_RUNS_PER_PLAN = 3
RUNS_FILE = ".render_runs"


def runs_key(directory: Path) -> list[float]:
    plan, feedback = directory / "clip_plan.json", directory / "review_feedback.json"
    return [plan.stat().st_mtime if plan.is_file() else 0.0, feedback.stat().st_mtime if feedback.is_file() else 0.0]


def render_runs(directory: Path) -> int:
    """Render runs so far on the episode's current plan and corrections."""
    try:
        saved = json.loads((directory / RUNS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(saved, dict) or saved.get("plan_mtime") != runs_key(directory):
        return 0
    return int(saved.get("runs", 0))


def count_run(directory: Path) -> int:
    runs = render_runs(directory) + 1
    (directory / RUNS_FILE).write_text(json.dumps({"plan_mtime": runs_key(directory), "runs": runs}), encoding="utf-8")
    return runs
