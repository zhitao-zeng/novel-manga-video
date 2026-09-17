from experiments.legacy import adoption
import novel_manga.application.repair.manager_dispatch as repair_manager_dispatch
import novel_manga.application.repair.manager_workers as repair_manager_workers
import json
import os
from pathlib import Path
import sys

import pytest

import novel_manga.repair.scheduling as schedule_rules
import novel_manga.application.repair.manager_flow as repair_manager_flow
import novel_manga.application.repair.manager_workers as repair_manager_workers
import novel_manga.application.production.runs as thin_runs


def info(**extra):
    return {"status": "done", "bad": 1, "unverified": 0, "flash_pending": 0, "can_fill": False,
            "ready": True, "deliverable": False, "held": False, **extra}


@pytest.mark.parametrize("tail,args,expected", [
    ("batch", ["/x/wy_gate.py"], 0),
    ("before repair:", ["/x/repair_clips_thin.py"], 1),
    ("before repair:", ["thin_batch.py"], 2),
    ("before repair:", ["thin_batch.py", "--review-only"], 3),
    ("before repair:", ["/x/wy_gate.py"], 3),
    ("after repair:", ["thin_batch.py"], 5),
    ("after repair:", ["thin_batch.py", "--review-only"], 6),
    ("after repair:\nafter retake 1:", ["thin_batch.py"], 8),
    ("after repair:\nafter retake 1:", ["/x/wy_gate.py"], 9),
])
def test_import_the_current_step_not_the_start_of_the_batch(tail, args, expected):
    assert adoption.legacy_step(args, tail) == expected


def test_all_lanes_share_episode_ownership(tmp_path):
    m = repair_manager_flow.Manager(tmp_path / "novel", tmp_path / "legacy")
    existing = m.add("fill", [2, 3], pid=os.getpid())
    m.info = {n: info() for n in range(1, 150)}
    m.info[145] = info(bad=0, can_fill=True, status="stale", ready=False)
    repair_manager_dispatch.schedule(m)
    groups = [j["episodes"] for j in m.state["jobs"]]
    assert sum(map(len, groups)) == len({n for g in groups for n in g})
    assert len([j for j in m.state["jobs"] if j["kind"] == "repair"]) == 24
    assert all(2 not in j["episodes"] and 3 not in j["episodes"] for j in m.state["jobs"] if j is not existing)
    assert all(len(j["episodes"]) == 1 for j in m.state["jobs"] if j["kind"] == "repair")


def test_next_fill_can_render_while_previous_fill_is_reviewed(tmp_path):
    m = repair_manager_flow.Manager(tmp_path / "novel", tmp_path / "legacy")
    first = m.add("fill", [1, 2], step=1, pid=os.getpid())
    m.info = {n: info(bad=0, ready=False, status="stale", can_fill=True) for n in range(1, 20)}
    repair_manager_dispatch.schedule(m)
    fills = [j for j in m.state["jobs"] if j["kind"] == "fill"]
    assert len(fills) == 13
    assert fills[0] is first and fills[1]["step"] == 0
    assert all(not set(j["episodes"]) & {1, 2} for j in fills[1:])
    repair_manager_dispatch.schedule(m)
    assert len([j for j in m.state["jobs"] if j["kind"] == "fill"]) == 13


def test_fill_review_backlog_has_a_bound(tmp_path):
    m = repair_manager_flow.Manager(tmp_path / "novel", tmp_path / "legacy")
    for n in range(1, 25):
        m.add("fill", [n], step=1)
    m.info = {n: info(bad=0, ready=False, status="stale", can_fill=True) for n in range(1, 50)}
    repair_manager_dispatch.schedule(m)
    assert len(m.state["jobs"]) == 24  # reserve bounded room for pending reviews


def test_smaller_new_batches_do_not_repartition_an_existing_large_batch(tmp_path):
    m = repair_manager_flow.Manager(tmp_path / "novel", tmp_path / "legacy")
    old = m.add("repair", list(range(1, 61)), step=5, pid=os.getpid())
    m.info = {n: info() for n in range(1, 100)}
    repair_manager_dispatch.schedule(m)
    assert old["episodes"] == list(range(1, 61)) and old["step"] == 5
    new = [j for j in m.state["jobs"] if j is not old]
    assert not new  # grandfather the old batch, without exceeding the new episode cap


@pytest.mark.parametrize("status,counts,bucket", [
    ("done", {"unchecked": 1, "failed": 1, "flash_pending": 0}, "not_fully_checked"),
    ("done", {"unchecked": 0, "failed": 1, "flash_pending": 0}, "checked_with_errors"),
    ("stale", {"unchecked": 0, "failed": 0, "flash_pending": 0}, "technical_pending"),
    ("done", {"unchecked": 0, "failed": 0, "flash_pending": 1}, "flash_confirmation"),
    ("done", {"unchecked": 0, "failed": 0, "flash_pending": 0}, "passed"),
])
def test_episode_progress_buckets_do_not_overlap(status, counts, bucket):
    assert schedule_rules.inspection_bucket(status, counts) == bucket


def test_second_pass_waits_for_qwen_and_primary_work_but_not_flash(tmp_path):
    m = repair_manager_flow.Manager(tmp_path / "novel", tmp_path / "legacy")
    m.state["passes"] = {"1": 1}
    rows = {1: info()}
    local = m.add("scan", [], source="local")
    flash = m.add("scan", [], source="flash")
    assert not schedule_rules.second_pass_ready(m.state, rows)
    local["status"] = "done"
    assert schedule_rules.second_pass_ready(m.state, rows)  # Flash may still be scanning
    fill = m.add("fill", [2])
    assert not schedule_rules.second_pass_ready(m.state, rows)
    fill["status"] = "done"
    assert schedule_rules.second_pass_ready(m.state, rows)
    rows[1]["unverified"] = 1
    assert not schedule_rules.second_pass_ready(m.state, rows)
    rows[1]["unverified"] = 0
    rows[1]["flash_pending"] = 1
    m.add("confirm", [1])
    assert schedule_rules.second_pass_ready(m.state, rows)  # candidates are confirmed alongside repair
    local["status"] = "held"
    assert not schedule_rules.second_pass_ready(m.state, rows)  # a failed primary scan is not a completed one


def test_confirmed_new_errors_still_need_a_first_repair_cycle(tmp_path):
    m = repair_manager_flow.Manager(tmp_path / "novel", tmp_path / "legacy")
    m.state["phase"] = 2
    m.state["passes"] = {"2": 1, "3": 2}
    m.info = {1: info(), 2: info(), 3: info()}
    repair_manager_dispatch.schedule(m)
    jobs = [j for j in m.state["jobs"] if j["kind"] == "repair"]
    assert [(j["episodes"], j["cycle"]) for j in jobs] == [([1], 1), ([2], 2)]
    assert all(3 not in j["episodes"] for j in jobs)  # exhausted errors remain visible, without endless retakes
    first = jobs[0]
    first["step"] = len(schedule_rules.REPAIR)
    repair_manager_workers.finish(m, first)
    assert m.state["passes"]["1"] == 1
    repair_manager_dispatch.schedule(m)
    assert any(j["episodes"] == [1] and j["cycle"] == 2 and j["status"] == "pending" for j in m.state["jobs"])


@pytest.mark.parametrize("rows", [
    {1: info(unverified=1)},
    {1: info(bad=0, can_fill=True)},
    {1: info()},
])
def test_flash_exemption_does_not_skip_unfinished_primary_work(tmp_path, rows):
    m = repair_manager_flow.Manager(tmp_path / "novel", tmp_path / "legacy")
    m.add("scan", [], source="flash")
    assert not schedule_rules.second_pass_ready(m.state, rows)


def test_restart_tracks_an_existing_renderer_then_continues_at_review(tmp_path, monkeypatch):
    m = repair_manager_flow.Manager(tmp_path / "novel", tmp_path / "legacy")
    job = m.add("repair", [12], step=8, pid=12345, adopted=True)
    m.save()
    restarted = repair_manager_flow.Manager(m.novel, m.legacy)
    monkeypatch.setattr(repair_manager_workers, 'alive', lambda pid: True)
    assert not repair_manager_workers.reap(restarted)
    assert restarted.state["jobs"][0]["step"] == 8
    monkeypatch.setattr(repair_manager_workers, 'alive', lambda pid: False)
    monkeypatch.setattr(thin_runs, 'episode_status', lambda *a: "done")
    assert repair_manager_workers.reap(restarted)
    assert restarted.state["jobs"][0]["step"] == 9
    assert restarted.state["jobs"][0]["status"] == "pending"
    assert restarted.state["passes"] == {}


def test_adoption_stops_controller_but_preserves_video_child(tmp_path, monkeypatch):
    novel, legacy = tmp_path / "novel", tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "wy_repair_chain2.state").write_text("ep:1\nep:2\n")
    (legacy / "wy_repair_chain2.log").write_text("===== batch: 3,4\nbefore repair: x\nafter repair: x\n")
    m = repair_manager_flow.Manager(novel, legacy)
    monkeypatch.setattr(adoption, "adoption_preview", lambda manager: {
        "controllers": [{"pid": 100, "args": ["bash", str(legacy / "wy_repair_chain2.sh")]}],
        "workers": [{"pid": 101, "args": ["python", "scripts/thin_batch.py", "--novel-dir", str(novel), "--chapters", "3,4"]}],
    })
    monkeypatch.setattr(adoption, 'alive', lambda pid: True)
    stopped = []
    monkeypatch.setattr(adoption.os, "kill", lambda pid, sig: stopped.append(pid))
    adoption.adopt(m)
    assert stopped == [100]
    assert m.state["passes"] == {"1": 1, "2": 1}
    assert m.state["jobs"][0]["pid"] == 101 and m.state["jobs"][0]["step"] == 5
    adoption.adopt(m)
    assert stopped == [100]  # a second call cannot import/reset the same work again


def test_repeated_worker_failure_is_held_and_keeps_its_claim_visible(tmp_path):
    m = repair_manager_flow.Manager(tmp_path / "novel", tmp_path / "legacy")
    job = m.add("audit", [1], pid=101)
    class Failed:
        def poll(self): return 1
    m.children[101] = Failed()
    for attempt in range(3):
        job.update(status="running", pid=101)
        repair_manager_workers.reap(m)
    assert job["status"] == "held" and job["step"] == 0 and job["episodes"] == [1]


def test_legacy_overlap_is_drained_before_launching_the_next_step(tmp_path, monkeypatch):
    m = repair_manager_flow.Manager(tmp_path / "novel", tmp_path / "legacy")
    m.add("fill", [2], pid=os.getpid())
    pending = m.add("repair", [1, 2], step=1)
    m.info = {1: info(), 2: info()}
    monkeypatch.setattr(repair_manager_workers, "command", lambda *args: pytest.fail("overlapping job must not be launched"))
    repair_manager_workers.launch(m)
    assert pending["status"] == "pending"
