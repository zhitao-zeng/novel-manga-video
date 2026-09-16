"""repair_manager_flow_thin responsibilities; existing job state and scheduling policy."""
from __future__ import annotations
from novel_manga.util import atomic_write_json
from novel_manga.util import read_json as read
from pathlib import Path
import fcntl
import os
import subprocess
import time
ROOT = Path(__file__).resolve().parents[1]
import novel_manga.repair.scheduling as schedule_rules
import repair_manager_dispatch_thin as repair_manager_dispatch
import repair_manager_state_thin as repair_manager_state
import repair_manager_workers_thin as repair_manager_workers

class Manager:
    def __init__(self, novel: Path, legacy: Path, state_dir: Path | None = None):
        self.novel = novel.resolve()
        self.legacy = legacy.resolve()
        self.directory = (state_dir or self.novel / "repair_manager").resolve()
        self.path = self.directory / "state.json"
        self.state = read(self.path) or {"novel": str(self.novel), "legacy_dir": str(self.legacy), "phase": 1,
                                      "passes": {}, "jobs": [], "next_id": 1, "scan_started": False}
        self.state.setdefault("fill_failures", {})
        self.state.setdefault("recovery_attempts", {})
        self.state.setdefault('targeted_recovery', [])
        scope = self.state.get('scope')
        self.episode_scope = set(map(int,scope['episodes'])) if scope is not None else None
        self.children: dict[int, subprocess.Popen] = {}
        self.info = {}
        self.inspection_rows = []
        self.last_refresh = 0.0
        self.last_delivery = 0.0

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state["updated_at"] = time.strftime("%F %T")
        atomic_write_json(self.path, self.state)

    def add(self, kind: str, episodes: list[int], *, step: int = 0, pid: int | None = None, **extra) -> dict:
        job = {"id": f"job-{self.state['next_id']:05d}", "kind": kind, "episodes": episodes, "step": step,
               "cycle": self.state["phase"], "status": "running" if pid else "pending", "pid": pid,
               "failures": 0, **extra}
        self.state["next_id"] += 1
        self.state["jobs"].append(job)
        return job


    def run(self, adopt: bool = False):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "manager.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SystemExit("repair manager is already running")
            self.state["pid"] = os.getpid()
            self.state["audit_policy"] = {"primary": "qwen", "supplementary": "shared_qwen_flash" if (self.directory / 'shared_audit.sqlite3').is_file() else "flash",
                                          "second_pass_waits_for": "qwen_and_first_pass_work"}
            self.state["dispatch_policy"] = {"repair_batch_size": schedule_rules.REPAIR_BATCH_SIZE,
                                             "fill_review_backlog": schedule_rules.FILL_REVIEW_BACKLOG,
                                             "unit": "episode", "repair_episodes": schedule_rules.REPAIR_EPISODES,
                                             "fill_episodes": schedule_rules.FILL_EPISODES, "stage_capacity": schedule_rules.STAGE_CAPACITY}
            if adopt:
                repair_manager_workers.adopt(self)
            if not self.state["scan_started"]:
                self.add("scan", [], source="local")
                if self.state.get("flash_env"):
                    self.add("scan", [], source="flash")
                self.state["scan_started"] = True
            while True:
                changed = repair_manager_workers.reap(self)
                if changed or time.monotonic() - self.last_refresh > 30:
                    repair_manager_state.refresh(self)
                paused = (self.directory / "pause").exists()
                self.state["status"] = "paused" if paused else "running"
                if not paused:
                    if self.state["phase"] == 1 and schedule_rules.second_pass_ready(self.state, self.info):
                        self.state["phase"] = 2
                        print("Qwen primary audit and first-pass work settled; starting residual pass. Flash continues independently.", flush=True)
                    repair_manager_dispatch.schedule(self)
                    repair_manager_workers.launch(self)
                if time.monotonic() - self.last_delivery > 180:
                    with (self.directory / "delivery.log").open("a") as out:
                        subprocess.run([str(ROOT / ".venv/bin/python"), "scripts/delivery_gate_thin.py", "--novel-dir", str(self.novel)],
                                       cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
                    self.last_delivery = time.monotonic()
                if not paused and self.state["phase"] == 2 and any(j["status"] in {"pending", "running"} and schedule_rules.supplementary(j) for j in self.state["jobs"]) and not any(j["status"] in {"pending", "running"} and not schedule_rules.supplementary(j) for j in self.state["jobs"]):
                    self.state["status"] = "monitoring_shared_audit" if (self.directory / 'shared_audit.sqlite3').is_file() else "monitoring_flash"
                if (not paused and self.state['summary'].get('preparation', {}).get('waiting')
                        and not any(j['status'] in {'pending', 'running'} for j in self.state['jobs'])):
                    self.state['status'] = 'waiting_preparation'
                    self.save()
                    time.sleep(5)
                    continue
                waiting_plan = any(j["status"] == "waiting_plan" for j in self.state["jobs"]) or bool(self.state.get("plan_queue"))
                if not paused and waiting_plan and not any(j["status"] in {"pending", "running"} for j in self.state["jobs"]):
                    self.state["status"] = "waiting_plan"
                self.save()
                if not paused and not waiting_plan and self.state["phase"] == 2 and not any(j["status"] in {"pending", "running"} for j in self.state["jobs"]):
                    # Current full delivery coverage supersedes historical
                    # failed scan jobs whose clips were checked by later work.
                    self.state["status"] = "complete" if self.state["summary"]["deliverable_precise"] == len(self.info) else "needs_attention"
                    self.save()
                    break
                if not paused and not any(j["status"] in {"pending", "running"} for j in self.state["jobs"]) and any(j["status"] == "held" for j in self.state["jobs"]):
                    self.state["status"] = "needs_attention"
                    self.save()
                    break
                time.sleep(5)
