"""The filler does not steal B's work or lose episodes after a skipped/failed render."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from thin_runs import count_run
import wuyue_filler_queue as queue
from wuyue_filler_queue import numbers, record, select


def episode(novel, n, *, stale=True):
    directory = novel / f"{novel.name}_{n}"
    directory.mkdir(parents=True)
    (directory / "clip_plan.json").write_text(json.dumps({"clips": []}))
    report = {"assembly": {"thin_passed": True}}
    if stale:
        report["clip_plan_fingerprint"] = "superseded plan"
    (directory / "thin_media_report.json").write_text(json.dumps(report))
    (directory / f"{directory.name}.mp4").write_bytes(b"existing video")
    return directory


def test_retry_failed_but_exclude_both_lanes_locks_and_finished(tmp_path, monkeypatch):
    novel, fix = tmp_path / "wuyue", tmp_path / "fix"
    fix.mkdir()
    for n in range(1, 7):
        episode(novel, n, stale=n != 1)
    (fix / "wy_repair_targets2.txt").write_text("3")
    (fix / "wy_repair_targets2b.txt").write_text("4")
    monkeypatch.setattr(queue, "running_scripts", lambda _: {str(fix / "wy_repair_chain2.sh"), str(fix / "wy_repair_chain2b.sh")})
    (fix / "wy_filler_tried.txt").write_text("2\n6\n6\n6\n")
    (novel / "wuyue_5" / ".render.lock").write_text(str(os.getpid()))
    result = select(novel, fix)
    assert result == {"selected": [2], "busy": [5, 4, 3], "held": [6], "unreadable": []}


def test_locked_skip_does_not_spend_an_attempt(tmp_path):
    novel, fix = tmp_path / "wuyue", tmp_path / "fix"
    fix.mkdir()
    locked = episode(novel, 1)
    (locked / ".render.lock").write_text(str(os.getpid()))
    episode(novel, 2)
    episode(novel, 3, stale=False)
    assert record(novel, fix, [1, 2, 3]) == {"finished": [3], "retry": [2], "locked": [1]}
    assert numbers(fix / "wy_filler_tried.txt") == [2, 3]


def test_renderer_exhausted_is_visible_even_without_filler_history(tmp_path):
    novel, fix = tmp_path / "wuyue", tmp_path / "fix"
    fix.mkdir()
    directory = episode(novel, 1)
    for _ in range(3):
        count_run(directory)
    assert select(novel, fix)["held"] == [1]


def test_old_target_file_does_not_reserve_work_after_lane_exits(tmp_path, monkeypatch):
    novel, fix = tmp_path / "wuyue", tmp_path / "fix"
    fix.mkdir()
    episode(novel, 1)
    (fix / "wy_repair_targets.txt").write_text("1")
    monkeypatch.setattr(queue, "running_scripts", lambda _: set())
    assert select(novel, fix)["selected"] == [1]
