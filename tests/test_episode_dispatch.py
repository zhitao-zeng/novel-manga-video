import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import clip_readiness as ready
import manage_repair_thin as manager


def info(**kwargs):
    return {"status": "done", "bad": 1, "unverified": 0, "flash_pending": 0, "can_fill": False,
            "ready": True, "deliverable": False, "held": False, **kwargs}


class Child:
    def __init__(self, pid, code=None):
        self.pid, self.code = pid, code

    def poll(self):
        return self.code


def fake_launch(m, monkeypatch):
    launched = []
    m.save()
    monkeypatch.setattr(m, "command", lambda job: (["worker", str(job["step"])], {}))
    def popen(command, **kw):
        child = Child(10000 + len(launched))
        launched.append((command, child))
        return child
    monkeypatch.setattr(manager.subprocess, "Popen", popen)
    return launched


def test_finished_episode_starts_review_while_its_peer_is_still_rendering(tmp_path, monkeypatch):
    m = manager.Manager(tmp_path / "nov", tmp_path / "legacy")
    fast = m.add("repair", [1], step=2, pid=101)
    slow = m.add("repair", [2], step=2, pid=102)
    m.children = {101: Child(101, 0), 102: Child(102)}
    m.info = {1: info(), 2: info()}
    monkeypatch.setattr(manager, "episode_status", lambda *a: "done")
    assert m.reap()
    assert fast["step"] == 3 and fast["status"] == "pending"
    assert slow["step"] == 2 and slow["status"] == "running"
    launched = fake_launch(m, monkeypatch)
    m.launch()
    assert fast["status"] == "running" and len(launched) == 1
    assert launched[0][0][-1] == "3" and slow["pid"] == 102
    assert not m.state["passes"]  # render completion is not completion of a repair cycle


def test_existing_batch_is_split_at_its_next_step_and_keeps_ownership(tmp_path, monkeypatch):
    m = manager.Manager(tmp_path / "nov", tmp_path / "legacy")
    old = m.add("repair", list(range(1, 13)), step=2, pid=101, cycle=1)
    m.children[101] = Child(101)
    m.split_pending()
    assert old["status"] == "running" and len(m.state["jobs"]) == 1
    m.children[101].code = 0
    m.reap()
    m.split_pending()
    children = [j for j in m.state["jobs"] if j.get("parent") == old["id"]]
    assert old["status"] == "split" and len(children) == 12
    assert all(j["step"] == 3 and j["cycle"] == 1 and len(j["episodes"]) == 1 for j in children)
    assert manager.active_episodes(m.state) == set(range(1, 13))
    assert not m.state["passes"]


@pytest.mark.parametrize("kind,step,limit", [("repair", 0, 12), ("repair", 1, 12), ("repair", 2, 24),
                                            ("fill", 0, 12), ("fill", 1, 12), ("confirm", 0, 6), ("audit", 0, 6)])
def test_per_episode_workers_keep_stage_concurrency_bounded(tmp_path, monkeypatch, kind, step, limit):
    m = manager.Manager(tmp_path / "nov", tmp_path / "legacy")
    for n in range(1, 35):
        m.add(kind, [n], step=step)
    m.info = {n: info() for n in range(1, 35)}
    launched = fake_launch(m, monkeypatch)
    m.launch()
    assert len(launched) == limit
    m.launch()
    assert len(launched) == limit


def test_legacy_review_workers_are_counted_against_new_workers(tmp_path, monkeypatch):
    m = manager.Manager(tmp_path / "nov", tmp_path / "legacy")
    m.add("repair", list(range(1, 13)), step=3, pid=101)
    for n in range(13, 25):
        m.add("repair", [n], step=3)
    m.info = {n: info() for n in range(1, 25)}
    launched = fake_launch(m, monkeypatch)
    m.launch()
    assert len(launched) == 6  # existing batch has six review workers


def blocked_episode(m, n=1):
    directory = m.novel / f"{m.novel.name}_{n}"
    directory.mkdir(parents=True)
    plan = {"clips": [{"clip_id": "clip_01", "kind": "video", "seconds_estimate": 40, "request_seconds": 15}]}
    (directory / "clip_plan.json").write_text(json.dumps(plan))
    ready.save_check(directory, plan, ready.plan_issues(plan))
    return directory, plan


def test_blocked_request_waits_without_spending_failure_or_repair_cycles(tmp_path, monkeypatch):
    m = manager.Manager(tmp_path / "nov", tmp_path / "legacy")
    directory, plan = blocked_episode(m)
    blocked = m.add("repair", [1], step=2, pid=101)
    good = m.add("repair", [2], step=2, pid=102)
    m.children = {101: Child(101, 3), 102: Child(102, 0)}
    monkeypatch.setattr(manager, "episode_status", lambda *a: "done")
    m.reap()
    assert blocked["status"] == "waiting_plan" and blocked["step"] == 2 and blocked["failures"] == 0
    assert good["status"] == "pending" and good["step"] == 3 and not m.state["passes"]
    assert 1 in manager.active_episodes(m.state)
    plan["clips"][0]["seconds_estimate"] = 5
    (directory / "clip_plan.json").write_text(json.dumps(plan))
    m.reap()
    assert blocked["status"] == "pending" and blocked["step"] == 2


def test_blocked_legacy_successor_goes_back_to_render_after_plan_fix(tmp_path, monkeypatch):
    m = manager.Manager(tmp_path / "nov", tmp_path / "legacy")
    blocked_episode(m)
    child = m.add("repair", [1], step=3, parent="legacy-batch")
    m.info = {1: info()}
    launched = fake_launch(m, monkeypatch)
    m.launch()
    assert not launched and child["status"] == "waiting_plan" and child["step"] == 2


@pytest.mark.parametrize("receipt,step,status", [({"returncode": 0}, 1, "pending"),
                                                ({"returncode": 7}, 0, "pending"), (None, 0, "pending")])
def test_restart_uses_worker_receipt_instead_of_assuming_success(tmp_path, monkeypatch, receipt, step, status):
    m = manager.Manager(tmp_path / "nov", tmp_path / "legacy")
    path = tmp_path / "result.json"
    if receipt is not None:
        path.write_text(json.dumps(receipt))
    m.add("repair", [1], pid=101, result=str(path))
    m.save()
    restarted = manager.Manager(m.novel, m.legacy)
    monkeypatch.setattr(manager, "alive", lambda pid: False)
    restarted.reap()
    j = restarted.state["jobs"][0]
    assert j["step"] == step and j["status"] == status
    assert j["failures"] == (0 if receipt and receipt["returncode"] == 0 else 1)


def test_worker_persists_a_real_nonzero_exit(tmp_path):
    result = tmp_path / "exit.json"
    run = subprocess.run([sys.executable, str(manager.ROOT / "scripts/run_repair_step.py"), "--result", str(result),
                          "--", sys.executable, "-c", "raise SystemExit(7)"], cwd=manager.ROOT)
    assert run.returncode == 7
    assert json.loads(result.read_text())["returncode"] == 7


def test_normal_worker_exit_without_completed_media_does_not_advance(tmp_path):
    m = manager.Manager(tmp_path / "nov", tmp_path / "legacy")
    job = m.add("fill", [1], pid=101)
    m.children[101] = Child(101, 0)
    m.reap()
    assert job["step"] == 0 and job["status"] == "pending" and job["failures"] == 1


def test_stale_legacy_media_is_rendered_before_its_review(tmp_path, monkeypatch):
    m = manager.Manager(tmp_path / "nov", tmp_path / "legacy")
    job = m.add("repair", [1], step=6, parent="legacy-batch")
    m.info = {1: info(status="stale", ready=False)}
    launched = fake_launch(m, monkeypatch)
    m.launch()
    assert job["step"] == 5 and launched[0][0][-1] == "5"


def test_waiting_inputs_do_not_take_worker_slots_or_block_other_second_passes(tmp_path):
    m = manager.Manager(tmp_path / "nov", tmp_path / "legacy")
    for n in range(1, 30):
        m.add("repair", [n], step=2)["status"] = "waiting_plan"
    m.info = {n: info(plan_blocked=True, ready=False) for n in range(1, 30)}
    m.info.update({n: info() for n in range(30, 60)})
    m.schedule()
    active = [j for j in m.state["jobs"] if j["status"] == "pending"]
    assert len(active) == 24 and all(j["episodes"][0] >= 30 for j in active)
    assert manager.second_pass_ready({"jobs": [], "passes": {}}, {1: info(plan_blocked=True)})
