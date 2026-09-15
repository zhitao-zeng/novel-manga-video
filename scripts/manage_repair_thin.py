#!/usr/bin/env python3
"""A single resumable owner for a novel's repair, rendering, precise review and audit.

run --adopt-legacy imports the current 雾月 batches and lets their renderer children
finish. status/pause/resume use the same state directory. No service or media is
deleted; generated state and logs live under <novel>/repair_manager/.
"""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from novel_manga.util import atomic_write_json
from repair_review_thin import current_takes, inspection_counts, load_evidence, read, reconcile, current_evidence
from thin_runs import RENDER_RUNS_PER_PLAN, episode_status, render_runs
from clip_readiness import current_blocks

REPAIR = ["check", "repair", "render", "review", "note1", "render1", "review1", "note2", "render2", "review2"]
FLOWS = {"repair": REPAIR, "fill": ["render", "review"], "audit": ["audit"], "confirm": ["confirm"],
         "recovery": ["recover", "render", "review"]}
REPAIR_BATCH_SIZE = 12  # one wave of the renderer's 12 episode workers; fewer slow-tail barriers
FILL_REVIEW_BACKLOG = 2  # keep rendering while a prior fill batch is reviewed, with bounded judge load
REPAIR_EPISODES = 2 * REPAIR_BATCH_SIZE
FILL_EPISODES = FILL_REVIEW_BACKLOG * REPAIR_BATCH_SIZE
STAGE_CAPACITY = {"repair_render": 24, "fill_render": 12, "repair_model": 12,
                  "fill_model": 12, "confirm": 6, "audit": 6}
CONTROLLERS = {"wy_repair_chain2.sh", "wy_repair_chain2b.sh", "wy_repair_pass2.sh", "wy_filler.sh",
               "wy_verify_run2.sh", "wy_verify_flash_all.sh", "wy_single_card_recovery.py"}


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return stat[0] != "Z"
    except OSError:
        return False


def processes() -> list[dict]:
    result = []
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = [s for s in path.read_bytes().decode(errors="replace").split("\0") if s]
            if args:
                result.append({"pid": int(path.parent.name), "args": args})
        except OSError:
            pass
    return result


def argument(args: list[str], name: str, default="") -> str:
    return args[args.index(name) + 1] if name in args else default


def episode_numbers(value: str) -> list[int]:
    return [int(s) for s in value.replace("\n", ",").split(",") if s.strip()]


def last_batch(log: Path) -> tuple[list[int], str]:
    text = log.read_text(errors="replace") if log.is_file() else ""
    at = text.rfind(" batch: ")
    if at < 0:
        return [], ""
    tail = text[at + len(" batch: "):]
    return episode_numbers(tail.splitlines()[0]), tail


def legacy_step(args: list[str], tail: str) -> int:
    if any(s.endswith("repair_clips_thin.py") for s in args):
        return 1
    if "after retake 1:" in tail:
        return 9 if "--review-only" in args or any(s.endswith("wy_gate.py") for s in args) else 8
    if "after repair:" in tail:
        return 6 if "--review-only" in args or any(s.endswith("wy_gate.py") for s in args) else 5
    if "--review-only" in args:
        return 3
    if any(s.endswith("wy_gate.py") for s in args):
        return 3 if "before repair:" in tail else 0
    return 2


def active_episodes(state: dict) -> set[int]:
    return {n for job in state["jobs"] if job["status"] in {"pending", "running", "waiting_plan"} for n in job.get("episodes", [])}


def stage_slots(job: dict) -> tuple[str, int] | None:
    if job["kind"] not in FLOWS:
        return None
    step = FLOWS[job["kind"]][job["step"]]
    if job["kind"] in {"audit", "confirm"}:
        return job["kind"], min(6, len(job["episodes"]))
    render = step.startswith("render")
    family = "repair" if job["kind"] == "recovery" else job["kind"]
    return f"{family}_{'render' if render else 'model'}", min(12 if render else 6, len(job["episodes"]))


def supplementary(job: dict) -> bool:
    return job["kind"] == "confirm" or (job["kind"] == "scan" and (job.get("source") == "flash" or str(job.get("source", "")).startswith("shared_")))


def second_pass_ready(state: dict, info: dict) -> bool:
    # Qwen is the full-book primary check. Flash and its confirmations keep
    # contributing findings while the residual pass runs; they are not a barrier.
    if any(j["status"] in {"pending", "running", "held"} and j["kind"] in {"scan", "drain"}
           and not supplementary(j) for j in state["jobs"]):
        return False
    if any(j["status"] in {"pending", "running"} and not supplementary(j) for j in state["jobs"]):
        return False
    return not any(r["unverified"] or
                   (r["bad"] and state["passes"].get(str(n), 0) == 0 and r.get('managed_clips',True)) or r["can_fill"]
                   for n, r in info.items() if not r.get("held") and not r.get("plan_blocked"))


def inspection_bucket(status: str, counts: dict) -> str:
    if counts["unchecked"]:
        return "not_fully_checked"
    if counts["failed"]:
        return "checked_with_errors"
    if status != "done":
        return "technical_pending"
    if counts["flash_pending"]:
        return "flash_confirmation"
    return "passed"


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

    def adoption_preview(self) -> dict:
        procs = processes()
        controllers = [p for p in procs if any(a == str(self.legacy / name) for a in p["args"] for name in CONTROLLERS)]
        workers = []
        for p in procs:
            args = p["args"]
            is_batch = any(a.endswith("/thin_batch.py") or a == "scripts/thin_batch.py" for a in args)
            if is_batch and argument(args, "--novel-dir"):
                target = Path(argument(args, "--novel-dir"))
                if (target if target.is_absolute() else ROOT / target).resolve() == self.novel:
                    workers.append(p)
            elif str(self.legacy / "wy_verify_clips.py") in args or str(self.legacy / "wy_gate.py") in args:
                workers.append(p)
            elif any(a.endswith("repair_clips_thin.py") for a in args) and argument(args, "--novel-dir"):
                if (ROOT / argument(args, "--novel-dir")).resolve() == self.novel:
                    workers.append(p)
        return {"controllers": controllers, "workers": workers,
                "a_batch": last_batch(self.legacy / "wy_repair_chain2.log")[0],
                "b_batch": last_batch(self.legacy / "wy_repair_chain2b.log")[0]}

    def adopt(self):
        if self.state.get("adopted_at"):
            return
        snapshot = self.adoption_preview()
        self.directory.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.directory / "legacy_handoff.json", snapshot)
        # Stop the shell/Python coordinators, not their media-producing children.
        for p in snapshot["controllers"]:
            if alive(p["pid"]):
                os.kill(p["pid"], signal.SIGTERM)
        for name in ("wy_repair_chain2.state", "wy_repair_chain2b.state"):
            path = self.legacy / name
            text = path.read_text() if path.is_file() else ""
            (self.directory / name).write_text(text)
            for line in text.splitlines():
                if line.startswith("ep:"):
                    self.state["passes"][line[3:]] = 1
        batches = [last_batch(self.legacy / name) for name in ("wy_repair_chain2.log", "wy_repair_chain2b.log")]
        adopted_batches = set()
        for p in snapshot["workers"]:
            args, pid = p["args"], p["pid"]
            if str(self.legacy / "wy_verify_clips.py") in args:
                mode = args[args.index(str(self.legacy / "wy_verify_clips.py")) + 1]
                if mode == "all":
                    environ = Path(f"/proc/{pid}/environ")
                    env = dict(s.split("=", 1) for s in environ.read_bytes().decode().split("\0") if "=" in s) if environ.exists() else {}
                    source = "flash" if "flash" in env.get("VERIFY_OUT", "") else "local"
                    self.add("scan", [], pid=pid, source=source, adopted=True,
                             log=str(self.legacy / ("wy_verify_flash_all.log" if source == "flash" else "wy_verify_all.log")))
                    if source == "flash":
                        self.state["flash_env"] = {k: env[k] for k in ("QWEN38_LOCAL_BASE_URL", "QWEN38_LOCAL_MODEL", "QWEN38_LOCAL_API_KEY_VAR", "QWEN38_LOCAL_STREAM") if k in env}
                else:
                    self.add("drain", [], pid=pid, adopted=True)
                continue
            episodes = episode_numbers(argument(args, "--chapters") or argument(args, "--episodes")) if "1-2043" not in args else []
            lane = next((i for i, (ns, _) in enumerate(batches) if ns and set(ns) == set(episodes)), None)
            is_gate = str(self.legacy / "wy_gate.py") in args
            if is_gate:
                # The separate gate is redundant; already-flushed precise results are imported below.
                if alive(pid):
                    os.kill(pid, signal.SIGTERM)
                if lane is not None and lane not in adopted_batches:
                    self.add("repair", episodes, step=legacy_step(args, batches[lane][1]), adopted=True)
                    adopted_batches.add(lane)
            elif lane is not None and lane not in adopted_batches:
                self.add("repair", episodes, step=legacy_step(args, batches[lane][1]), pid=pid if alive(pid) else None, adopted=True)
                adopted_batches.add(lane)
            else:
                self.add("fill", episodes, step=1 if "--review-only" in args else 0, pid=pid if alive(pid) else None, adopted=True)
        for lane, (episodes, tail) in enumerate(batches):
            if lane in adopted_batches or not episodes:
                continue
            if "BATCH RESULT" in tail or "after retake 2:" in tail:
                for n in episodes:
                    self.state["passes"][str(n)] = 1
            else:
                step = 7 if "after retake 1:" in tail else 4 if "after repair:" in tail else 1 if "before repair:" in tail else 0
                self.add("repair", episodes, step=step, adopted=True)
        self.state["scan_started"] = any(j["kind"] == "scan" for j in self.state["jobs"])
        self.state["adopted_at"] = time.strftime("%F %T")
        self.save()

    def refresh(self, *, write: bool = True):
        local, flash = current_evidence(self.legacy, self.directory)
        busy = active_episodes(self.state)
        held = {n for j in self.state["jobs"] if j["status"] in {"held", "needs_attention"} for n in j["episodes"]}
        held.update(int(n) for n, count in self.state["fill_failures"].items() if count >= 3)
        info = {}
        inspection_rows = []
        plan_queue = {}
        for directory in self.novel.glob(f"{self.novel.name}_*"):
            suffix = directory.name.rsplit("_", 1)[-1]
            if not directory.is_dir() or not suffix.isdigit():
                continue
            n = int(suffix)
            if self.episode_scope is not None and n not in self.episode_scope:
                continue
            try:
                status = episode_status(directory, True)
                blocked = current_blocks(directory) if status == "plan_blocked" else {}
                if blocked:
                    plan_queue[str(n)] = blocked
                review, takes = reconcile(directory, local, flash, write=write and n not in busy)
                from repair_history import observe, publication_pending, publish_if_ready
                if write and n not in busy:
                    observe(directory, review, takes)
                    publish_if_ready(directory, review, takes)
                pending_publication = publication_pending(directory)
                media = read(directory / "thin_media_report.json", {}) if status == "done_with_warnings" else {}
                black_targets = read(directory / "technical_repair.json", {}).get("black_clips", []) if media else []
                exposure_due = any(c.get("clip_id") in black_targets and (c.get("selected") or {}).get("black_check_policy") not in {2, 3}
                                   for c in media.get("clips", []))
                plan = read(directory / "clip_plan.json", {})
                expected = [c["clip_id"] for c in plan.get("clips", []) if c.get("kind") == "video"]
                clips = review.get("clips") or {}
                inspected = inspection_counts(review, takes, expected)
                unverified = inspected["unchecked"]
                inspection_rows.append({"episode": n, "status": status, "bucket": inspection_bucket(status, inspected),
                                        "processed_cycles": self.state["passes"].get(str(n), 0), **inspected})
                bad = len(review.get("feedback") or {})
                ready = status in {"done", "done_with_warnings"}
                from managed_repair_thin import candidates as repair_candidates
                managed_clips,managed_blocked = repair_candidates(directory,review) if (ready and bad) or blocked else ([],{})
                flash_pending = sum(bool(v.get("flash_pending")) for v in clips.values())
                info[n] = {"status": status, "bad": bad, "unverified": unverified if ready else 0,
                           "flash_pending": flash_pending,
                           "can_fill": status in {"stale", "pending", "clips_failed"} and render_runs(directory) < RENDER_RUNS_PER_PLAN and n not in held,
                           "deliverable": status == "done" and bad == 0 and unverified == 0 and not flash_pending and not pending_publication,
                           "held": n in held, "ready": ready, "plan_blocked": bool(blocked), "exposure_due": exposure_due,
                           'managed_clips':managed_clips,'managed_blocked':managed_blocked}
            except (OSError, ValueError, KeyError):
                info[n] = {"status": "unreadable", "bad": 0, "unverified": 0, "flash_pending": 0,
                           "can_fill": False, "deliverable": False, "held": True, "ready": False,
                           'managed_clips':[],'managed_blocked':{}}
                inspection_rows.append({"episode": n, "status": "unreadable", "bucket": "unreadable",
                                        "total": 0, "passed": 0, "failed": 0, "unchecked": 0,
                                        "unconfirmed_candidates": 0, "flash_pending": 0, "processed_cycles": self.state["passes"].get(str(n), 0)})
        self.info = info
        for job in self.state["jobs"]:
            if (job["kind"] == "recovery" and job["status"] == "done"
                    and job.get("outcome") in {None, "awaiting_current_delivery_check", "awaiting_review_or_publication"}):
                row = info.get(job["episodes"][0], {})
                job["outcome"] = ("passed" if row.get("deliverable") else "plan_blocked" if row.get("plan_blocked")
                                  else "visual_error" if row.get("bad") else "technical_error" if row.get("status") != "done"
                                  else "awaiting_review_or_publication")
        self.state["plan_queue"] = plan_queue
        self.inspection_rows = sorted(inspection_rows, key=lambda r: r["episode"])
        self.state["summary"] = {"total": len(info), "deliverable_precise": sum(r["deliverable"] for r in info.values()),
                                 "must_fix_clips": sum(r["bad"] for r in info.values()),
                                 "unverified_clips": sum(r["unverified"] for r in info.values()),
                                 "technical": dict(Counter(r["status"] for r in info.values())),
                                 "held_episodes": sorted(n for n, r in info.items() if r["held"]),
                                 "plan_blocked_episodes": sorted(map(int, plan_queue)),
                                 "plan_blocked_clips": sum(len(clips) for clips in plan_queue.values()),
                                 'repair_ready_clips':sum(len(r['managed_clips']) for r in info.values()),
                                 'repair_blocked_clips':sum(len(r['managed_blocked']) for r in info.values()),
                                 "residual_episodes": sorted(n for n, r in info.items() if r["bad"] and self.state["passes"].get(str(n), 0) >= 2)}
        self.state["summary"]["inspection"] = {
            "episode_buckets": dict(Counter(r["bucket"] for r in inspection_rows)),
            "clips": {k: sum(r[k] for r in inspection_rows) for k in
                      ["total", "passed", "failed", "unchecked", "unconfirmed_candidates", "flash_pending"]},
            "episodes_with_confirmed_errors": sum(r["failed"] > 0 for r in inspection_rows),
            "error_episodes_still_partly_unchecked": sum(r["failed"] > 0 and r["unchecked"] > 0 for r in inspection_rows),
            "episodes_with_no_precise_check": sum(r["total"] > 0 and r["unchecked"] == r["total"] for r in inspection_rows),
            "processed_episodes_now_passed": sum(r["bucket"] == "passed" and r["processed_cycles"] > 0 for r in inspection_rows),
        }
        self.state["summary"]["recovery"] = {
            kind: dict(Counter(j["status"] for j in self.state["jobs"]
                               if j["kind"] == "recovery" and j.get("recovery_kind") == kind))
            for kind in ("plan", "references", "technical", "residual", "identity", "binding", "speech", "review", 'source','managed')}
        self.state["summary"]["recovery_outcomes"] = dict(Counter(
            j.get("outcome", "unknown") for j in self.state["jobs"] if j["kind"] == "recovery" and j["status"] == "done"))
        from shared_audit_thin import summary as audit_summary
        self.state["summary"]["shared_audit"] = audit_summary(self.directory / 'shared_audit.sqlite3')
        self.last_refresh = time.monotonic()

    def command(self, job: dict) -> tuple[list[str], dict]:
        from thin_batch import load_dotenv, reference_image_env
        load_dotenv(ROOT / ".env")
        env = dict(os.environ, PYTHONPATH="src:scripts", NOVEL_VIDEO_MODEL="minimax-h3-ref2va-turbo",
                   NOVEL_LOCAL_H3_URL="pool", NOVEL_INFLIGHT_POOL="h3pool", NOVEL_REVIEW_MODE="verify",
                   NOVEL_INFLIGHT_DIR=str(self.legacy.parent / "inflight/h3pool"), NOVEL_CLIP_SECONDS_MAX="15", PHANROUTER_VIDEO_KEY_VAR="")
        env.update(reference_image_env(env))
        python = str(ROOT / ".venv/bin/python")
        episodes = ",".join(map(str, job["episodes"]))
        if job["kind"] == "scan":
            source = job["source"]
            if source in {"shared_qwen", "shared_flash"}:
                lane = source.removeprefix('shared_')
                if lane == 'flash':
                    env.update(self.state.get('flash_env') or {})
                return [python, 'scripts/shared_audit_thin.py', '--novel-dir', str(self.novel), '--state-dir', str(self.directory),
                        '--queue', str(self.directory / 'shared_audit.sqlite3'), '--lane', lane,
                        '--workers', '2' if lane == 'flash' else '8'], env
            output = self.directory / f"scan_{source}.jsonl"
            prior = self.legacy / ("wy_verify_flash.jsonl" if source == "flash" else "wy_verify.jsonl")
            if not output.exists() and prior.is_file():
                shutil.copy2(prior, output)  # resume a scanner from its written take records
            if source == "flash":
                env.update(self.state.get("flash_env") or {})
            return [python, "scripts/verify_clips_thin.py", "--novel-dir", str(self.novel), "--mode", "all", "--workers",
                    "2" if source == "flash" else "8", "--judge-tag", source, "--out", str(output)], env
        step = FLOWS[job["kind"]][job["step"]]
        if job['kind']=='repair' and step in {'repair','note1','note2'} and len(job['episodes'])==1:
            n=job['episodes'][0]
            return [python,'scripts/prepare_recovery_thin.py','--episode-dir',str(self.novel/f'{self.novel.name}_{n}'),
                    '--kind','managed','--job-id',f"{job['id']}-step{job['step']}"],env
        if step == "recover":
            n = job["episodes"][0]
            return [python, "scripts/prepare_recovery_thin.py", "--episode-dir", str(self.novel / f"{self.novel.name}_{n}"),
                    "--kind", job["recovery_kind"], "--job-id", job["id"]], env
        workers = "1" if len(job["episodes"]) == 1 else "6"
        if step in {"check", "review", "review1", "review2", "audit", "confirm"}:
            scope = "all" if job["kind"] == "recovery" else {"check": "candidates", "audit": "all", "confirm": "flash"}.get(step, "changed")
            return [python, "scripts/repair_review_thin.py", "--novel-dir", str(self.novel), "--episodes", episodes,
                    "--scope", scope, "--state-dir", str(self.directory), "--legacy-dir", str(self.legacy), "--workers", workers] + (
                    ['--max-tokens', str(job['review_tokens'])] if job.get('review_tokens') else []), env
        if step == "repair":
            return [python, "scripts/repair_clips_thin.py", "--novel-dir", str(self.novel), "--episodes", episodes, "--workers", workers, "--apply"], env
        if step.startswith("note"):
            return [python, "scripts/apply_review_feedback.py", "--novel-dir", str(self.novel), "--episodes", episodes, "--must-fix", "--apply"], env
        return [python, "scripts/thin_batch.py", "--novel-dir", str(self.novel), "--chapters", episodes,
                "--stage", "render", "--tier", "fast", "--merge", "1", "--parallel", "1" if len(job["episodes"]) == 1 else "12", "--workers", "0",
                "--card-parallel", "1" if len(job["episodes"]) == 1 else "6", "--no-recurring-cards",
                "--inflight", "24", "--plan-mode", "15", "--no-prescreen", "--prune"] + (
                    ["--rerender"] if job["kind"] == "recovery" and job.get("recovery_kind") == "technical" else []) + (
                    ["--cache-only"] if job.get("cache_only") else []), env

    def finish(self, job: dict):
        if job["kind"] == "recovery":
            # A recovery cycle finishing is not a claim that the episode passed.
            job["outcome"] = "awaiting_current_delivery_check"
        if job["kind"] == "repair" and job["step"] > 1:
            for n in job["episodes"]:
                self.state["passes"][str(n)] = max(job["cycle"], self.state["passes"].get(str(n), 0))
        if job["kind"] == "fill":
            for n in job["episodes"]:
                try:
                    ready = episode_status(self.novel / f"{self.novel.name}_{n}", True) in {"done", "done_with_warnings"}
                except (OSError, ValueError, KeyError):
                    ready = False
                self.state["fill_failures"][str(n)] = 0 if ready else self.state["fill_failures"].get(str(n), 0) + 1
        job.update(status="done", pid=None, finished_at=time.strftime("%F %T"))

    def reap(self):
        changed = False
        for job in self.state["jobs"]:
            if job["status"] == "waiting_plan":
                if not any(current_blocks(self.novel / f"{self.novel.name}_{n}") for n in job["episodes"]):
                    job.update(status="pending", failures=0)
                    job.pop("blocked_clips", None)
                    changed = True
                continue
            if job["status"] != "running":
                continue
            pid = job["pid"]
            child = self.children.get(pid)
            if child is not None:
                code = child.poll()
                if code is None:
                    continue
            elif alive(pid):
                continue
            else:
                result = read(Path(job["result"])) if job.get("result") else None
                # New workers leave a receipt. Its absence cannot mean success.
                # In-flight old workers retain the previous handoff while draining.
                code = int(result["returncode"]) if result else (1 if job.get("result") else 0)
                if job["kind"] == "scan" and not str(job.get('source', '')).startswith('shared_'):
                    lines = Path(job["log"]).read_text(errors="replace").splitlines()
                    code = 0 if lines and lines[-1].strip() == "done" else 1
            changed = True
            job["pid"] = None
            if job["kind"] in FLOWS and FLOWS[job["kind"]][job["step"]].startswith("render"):
                blocked = {str(n): rows for n in job["episodes"]
                           if (rows := current_blocks(self.novel / f"{self.novel.name}_{n}"))}
                if blocked and len(job["episodes"]) == 1:
                    job.update(status="waiting_plan", blocked_clips=blocked, failures=0)
                    continue
                if code == 0 and len(job["episodes"]) == 1:
                    n = job["episodes"][0]
                    if episode_status(self.novel / f"{self.novel.name}_{n}", True) not in {"done", "done_with_warnings"}:
                        code = 1  # a batch CLI exiting normally does not prove that its media finished
            if code == 4 and (job["kind"] == "recovery" or (job['kind'] == 'scan' and str(job.get('source', '')).startswith('shared_'))):
                job.update(status="needs_attention", last_exit=code)
                continue
            if code != 0:
                job["failures"] += 1
                job.update(status="pending" if job["failures"] < 3 else "held", last_exit=code)
                continue
            job["failures"] = 0
            managed_preparation = (job['kind']=='repair' and FLOWS['repair'][job['step']] in {'repair','note1','note2'}
                                   or job['kind']=='recovery' and job['step']==0 and job.get('recovery_kind') in {'managed','residual','references'})
            if managed_preparation and len(job['episodes'])==1:
                n=job['episodes'][0]
                preparation_id=f"{job['id']}-step{job['step']}" if job['kind']=='repair' else job['id']
                preparation=read(self.novel/f'{self.novel.name}_{n}'/'repair_history'/f'preparation-{preparation_id}.json',{})
                if preparation.get('skip_render'):
                    self.finish(job)
                    continue
            if job["kind"] in {"scan", "drain"}:
                self.finish(job)
                continue
            job["step"] += 1
            if job["step"] >= len(FLOWS[job["kind"]]):
                self.finish(job)
            else:
                job["status"] = "pending"
        return changed

    def split_pending(self):
        """A legacy batch keeps its child; split only after that child has exited."""
        for job in list(self.state["jobs"]):
            if job["status"] != "pending" or job["kind"] not in FLOWS or len(job["episodes"]) <= 1:
                continue
            children = [self.add(job["kind"], [n], step=job["step"], cycle=job["cycle"],
                                 failures=job["failures"], parent=job["id"])["id"] for n in job["episodes"]]
            job.update(status="split", pid=None, children=children, finished_at=time.strftime("%F %T"))

    def schedule_recovery(self):
        """Consume stopped work fairly; a changed strategy gets one bounded cycle."""
        active = sum(len(j["episodes"]) for j in self.state["jobs"]
                     if j["kind"] in {"repair", "recovery"} and j["status"] in {"pending", "running"})
        busy = active_episodes(self.state)
        waiting = {n: j for j in self.state["jobs"] if j["status"] == "waiting_plan" for n in j["episodes"]}
        # Clear missing secondary-card dependencies before starting more new
        # targets. Otherwise a long initial list starves these stopped jobs.
        for n,row in sorted(self.info.items()):
            if active >= REPAIR_EPISODES:
                break
            reasons=[reason for values in self.state.get('plan_queue',{}).get(str(n),{}).values() for reason in values]
            if not any(reason.startswith('asset:') and reason.endswith('/expressions.jpeg') for reason in reasons):
                continue
            if row.get('held') or (n in busy and n not in waiting):
                continue
            old=waiting.pop(n,None)
            if old:
                old.update(status='superseded',finished_at=time.strftime('%F %T'))
            job=self.add('recovery',[n],recovery_kind='references')
            if old:
                job['replaces']=old['id']
            used=self.state['recovery_attempts'].setdefault(str(n),{})
            used['references']=used.get('references',0)+1
            busy.add(n);active+=1
        for item in list(self.state['targeted_recovery']):
            if active >= REPAIR_EPISODES:
                break
            n = item['episode']
            if n in busy or self.info.get(n, {}).get('held'):
                continue
            options = {k:v for k,v in item.items() if k not in {'episode', 'method'}}
            self.add('recovery', [n], recovery_kind=item['method'], **options)
            used = self.state['recovery_attempts'].setdefault(str(n), {})
            used[item['method']] = used.get(item['method'], 0) + 1
            self.state['targeted_recovery'].remove(item)
            busy.add(n)
            active += 1
        queues = {k: [] for k in ("plan", "technical", "residual",'managed')}
        exposure = set()
        for n, row in sorted(self.info.items()):
            used = self.state["recovery_attempts"].get(str(n), {})
            if row.get("held") or (n in busy and n not in waiting):
                continue
            block_reasons = [reason for reasons in self.state.get('plan_queue', {}).get(str(n), {}).values() for reason in reasons]
            request_only = bool(block_reasons) and all(reason.startswith('request:') for reason in block_reasons)
            kind = ('managed' if row.get('plan_blocked') and request_only and row.get('managed_clips') else "plan" if row.get("plan_blocked") else "technical" if row["status"] == "done_with_warnings"
                    else "residual" if row["ready"] and row["bad"] and self.state["passes"].get(str(n), 0) >= 2 else None)
            cache_exposure = kind == "technical" and used.get(kind) and row.get("exposure_due") and not used.get("exposure")
            if kind and (kind=='managed' or not used.get(kind) or cache_exposure):
                # A waiting record alone is not enough: prepare needs the source script.
                if kind == "plan" and not (self.novel / f"{self.novel.name}_{n}" / "chapter_script.json").is_file():
                    continue
                queues[kind].append(n)
                if cache_exposure:
                    exposure.add(n)
        while active < REPAIR_EPISODES and any(queues.values()):
            for kind, queue in queues.items():
                if not queue or active >= REPAIR_EPISODES:
                    continue
                n = queue.pop(0)
                if n in waiting:
                    old = waiting[n]
                    # Transfer the old waiting job's ownership; no competing worker.
                    old.update(status="superseded", finished_at=time.strftime("%F %T"))
                job = self.add("recovery", [n], recovery_kind=kind, **(
                    {"step": 1, "cache_only": True, "correction": "restore_underexposed_scene"} if n in exposure else {}))
                if n in waiting:
                    job["replaces"] = waiting[n]["id"]
                self.state["recovery_attempts"].setdefault(str(n), {})["exposure" if n in exposure else kind] = 1
                active += 1

    def schedule(self):
        self.split_pending()
        self.schedule_recovery()
        busy = active_episodes(self.state)
        busy.update(item['episode'] for item in self.state['targeted_recovery'])
        def eligible(n):
            return n not in busy and not self.info[n]["held"]
        repair_count = sum(len(j["episodes"]) for j in self.state["jobs"] if j["kind"] in {"repair", "recovery"} and j["status"] in {"pending", "running"})
        candidates = [n for n in sorted(self.info) if eligible(n) and self.info[n]["ready"] and self.info[n]["bad"]
                      and self.info[n].get('managed_clips',self.state['passes'].get(str(n),0)<self.state['phase'])]
        for n in candidates[:max(0, REPAIR_EPISODES - repair_count)]:
            self.add("repair", [n], cycle=min(self.state['phase'],self.state["passes"].get(str(n), 0) + 1))
            busy.add(n)
        fills = [j for j in self.state["jobs"] if j["kind"] == "fill" and j["status"] in {"pending", "running"}]
        free_fills = min(REPAIR_BATCH_SIZE - sum(len(j["episodes"]) for j in fills if j["step"] == 0),
                         FILL_EPISODES - sum(len(j["episodes"]) for j in fills))
        for n in [n for n in sorted(self.info, reverse=True) if eligible(n) and self.info[n]["can_fill"]][:max(0, free_fills)]:
            self.add("fill", [n])
            busy.add(n)
        confirms = sum(len(j["episodes"]) for j in self.state["jobs"] if j["kind"] == "confirm" and j["status"] in {"pending", "running"})
        for n in [n for n in sorted(self.info) if eligible(n) and self.info[n]["ready"] and self.info[n]["flash_pending"]][:max(0, 12 - confirms)]:
            self.add("confirm", [n])
            busy.add(n)
        local_scanning = any(j["kind"] == "scan" and j.get("source") == "local" and j["status"] in {"pending", "running"} for j in self.state["jobs"])
        if not local_scanning and not any(j["kind"] == "audit" and j["status"] in {"pending", "running"} for j in self.state["jobs"]):
            group = [n for n in sorted(self.info) if eligible(n) and self.info[n]["ready"] and self.info[n]["unverified"]][:12]
            if group:
                for n in group:
                    self.add("audit", [n])

    def launch(self):
        for job in self.state["jobs"]:
            if job["status"] != "pending":
                continue
            # Existing legacy batches can overlap during adoption. Let their old
            # child finish, then serialize the next step on every claimed episode.
            if any(other["status"] == "running" and set(job["episodes"]) & set(other["episodes"]) for other in self.state["jobs"]):
                continue
            blocked = {str(n): rows for n in job["episodes"]
                       if (rows := current_blocks(self.novel / f"{self.novel.name}_{n}"))}
            if blocked and job["kind"] in {"repair", "fill", "recovery"} and FLOWS[job["kind"]][job["step"]] != "recover":
                # An old batch can finish rendering while the new manager is
                # taking over. Its blocked episode resumes at generation after
                # its inputs change, not at a review of the old/missing video.
                renders = [i for i, step in enumerate(FLOWS[job["kind"]]) if step.startswith("render") and i <= job["step"]]
                job.update(status="waiting_plan", blocked_clips=blocked, step=renders[-1] if renders else job["step"])
                continue
            if job["kind"] in {"repair", "fill", "recovery"} and FLOWS[job["kind"]][job["step"]].startswith("review"):
                if any(self.info.get(n, {}).get("status") in {"stale", "pending", "clips_failed", "plan_blocked"} for n in job["episodes"]):
                    job["step"] = max(i for i, step in enumerate(FLOWS[job["kind"]]) if step.startswith("render") and i < job["step"])
            if job["kind"] == "repair" and job["step"] in {1, 4, 7}:
                if all(not self.info.get(n, {}).get("bad", 1) for n in job["episodes"]):
                    self.finish(job)
                    continue
            slot = stage_slots(job)
            if slot:
                used = sum(other_slot[1] for other in self.state["jobs"] if other["status"] == "running"
                           and (other_slot := stage_slots(other)) and other_slot[0] == slot[0])
                if used + slot[1] > STAGE_CAPACITY[slot[0]]:
                    continue
            command, env = self.command(job)
            log = self.directory / f"{job['id']}-step{job['step']:02d}.log"
            job["launches"] = job.get("launches", 0) + 1
            result = self.directory / f"{job['id']}-step{job['step']:02d}-run{job['launches']}.json"
            wrapped = [str(ROOT / ".venv/bin/python"), "scripts/run_repair_step.py", "--result", str(result), "--", *command]
            with log.open("a") as stream:
                child = subprocess.Popen(wrapped, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                         stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            self.children[child.pid] = child
            job.update(status="running", pid=child.pid, log=str(log), result=str(result), adopted=False, started_at=time.strftime("%F %T"))
            self.save()
            print(f"{time.strftime('%H:%M:%S')} {job['id']} {job['kind']} step {job['step']} ({len(job['episodes'])} episodes)", flush=True)

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
            self.state["dispatch_policy"] = {"repair_batch_size": REPAIR_BATCH_SIZE,
                                             "fill_review_backlog": FILL_REVIEW_BACKLOG,
                                             "unit": "episode", "repair_episodes": REPAIR_EPISODES,
                                             "fill_episodes": FILL_EPISODES, "stage_capacity": STAGE_CAPACITY}
            if adopt:
                self.adopt()
            if not self.state["scan_started"]:
                self.add("scan", [], source="local")
                if self.state.get("flash_env"):
                    self.add("scan", [], source="flash")
                self.state["scan_started"] = True
            while True:
                changed = self.reap()
                if changed or time.monotonic() - self.last_refresh > 30:
                    self.refresh()
                paused = (self.directory / "pause").exists()
                self.state["status"] = "paused" if paused else "running"
                if not paused:
                    if self.state["phase"] == 1 and second_pass_ready(self.state, self.info):
                        self.state["phase"] = 2
                        print("Qwen primary audit and first-pass work settled; starting residual pass. Flash continues independently.", flush=True)
                    self.schedule()
                    self.launch()
                if time.monotonic() - self.last_delivery > 180:
                    with (self.directory / "delivery.log").open("a") as out:
                        subprocess.run([str(ROOT / ".venv/bin/python"), "scripts/delivery_gate_thin.py", "--novel-dir", str(self.novel)],
                                       cwd=ROOT, stdout=out, stderr=subprocess.STDOUT)
                    self.last_delivery = time.monotonic()
                if not paused and self.state["phase"] == 2 and any(j["status"] in {"pending", "running"} and supplementary(j) for j in self.state["jobs"]) and not any(j["status"] in {"pending", "running"} and not supplementary(j) for j in self.state["jobs"]):
                    self.state["status"] = "monitoring_shared_audit" if (self.directory / 'shared_audit.sqlite3').is_file() else "monitoring_flash"
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "status", "progress", "pause", "resume", "preview"])
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--legacy-dir", type=Path, default=Path("/mnt/disk1/zengzhitao/tmp/fix"))
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--adopt-legacy", action="store_true")
    args = parser.parse_args()
    manager = Manager(args.novel_dir, args.legacy_dir, args.state_dir)
    if args.action == "preview":
        snapshot = manager.adoption_preview()
        print(json.dumps({"controllers_to_retire": [{"pid": p["pid"], "entry": p["args"][-1]} for p in snapshot["controllers"]],
                          "workers_to_keep": snapshot["workers"], "current_batches": {"A": snapshot["a_batch"], "B": snapshot["b_batch"]}}, ensure_ascii=False, indent=2))
    elif args.action == "progress":
        # Read-only with respect to production files and the coordinator's state.
        manager.refresh(write=False)
        report = {"generated_at": time.strftime("%F %T"), "total_episodes": len(manager.inspection_rows),
                  **manager.state["summary"]["inspection"], "episodes": manager.inspection_rows}
        manager.directory.mkdir(parents=True, exist_ok=True)
        path = manager.directory / "inspection_progress.json"
        atomic_write_json(path, report)
        print(json.dumps({k: v for k, v in report.items() if k != "episodes"}, ensure_ascii=False, indent=2))
        print(f"Details: {path}")
    elif args.action == "status":
        state = manager.state
        print(json.dumps({"status": state.get("status", "not_started"), "alive": alive(state.get("pid")), "phase": state["phase"],
                          "updated_at": state.get("updated_at"), "audit_policy": state.get("audit_policy"),
                          "dispatch_policy": state.get("dispatch_policy"), "summary": state.get("summary"),
                          "jobs": [{"id": j["id"], "kind": j["kind"], "stage": FLOWS[j["kind"]][j["step"]] if j["kind"] in FLOWS else j.get("source", j["kind"]),
                                    "status": j["status"], "episode_count": len(j["episodes"]), "pid": j.get("pid"),
                                    "failures": j.get("failures", 0), "log": j.get("log")}
                                   for j in state["jobs"] if j["status"] not in {"done", "split", "superseded"}]}, ensure_ascii=False, indent=2))
    elif args.action in {"pause", "resume"}:
        manager.directory.mkdir(parents=True, exist_ok=True)
        pause = manager.directory / "pause"
        pause.write_text(time.strftime("%F %T")) if args.action == "pause" else pause.unlink(missing_ok=True)
        print("pause requested; current steps may finish" if args.action == "pause" else "resume requested")
    else:
        manager.run(args.adopt_legacy)


if __name__ == "__main__":
    main()
