"""repair_manager_workers_thin responsibilities; existing job state and scheduling policy."""
from __future__ import annotations
from novel_manga.util import atomic_write_json
from novel_manga.util import read_json as read
from pathlib import Path
import os
import shutil
import signal
import subprocess
import time
ROOT = Path(__file__).resolve().parents[1]
import clip_readiness as clip_readiness
import novel_manga.repair.scheduling as schedule_rules
import thin_runs as thin_runs

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


def adoption_preview(manager) -> dict:
    procs = processes()
    controllers = [p for p in procs if any(a == str(manager.legacy / name) for a in p["args"] for name in CONTROLLERS)]
    workers = []
    for p in procs:
        args = p["args"]
        is_batch = any(a.endswith("/thin_batch.py") or a == "scripts/thin_batch.py" for a in args)
        if is_batch and argument(args, "--novel-dir"):
            target = Path(argument(args, "--novel-dir"))
            if (target if target.is_absolute() else ROOT / target).resolve() == manager.novel:
                workers.append(p)
        elif str(manager.legacy / "wy_verify_clips.py") in args or str(manager.legacy / "wy_gate.py") in args:
            workers.append(p)
        elif any(a.endswith("repair_clips_thin.py") for a in args) and argument(args, "--novel-dir"):
            if (ROOT / argument(args, "--novel-dir")).resolve() == manager.novel:
                workers.append(p)
    return {"controllers": controllers, "workers": workers,
            "a_batch": last_batch(manager.legacy / "wy_repair_chain2.log")[0],
            "b_batch": last_batch(manager.legacy / "wy_repair_chain2b.log")[0]}


def adopt(manager):
    if manager.state.get("adopted_at"):
        return
    snapshot = adoption_preview(manager)
    manager.directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(manager.directory / "legacy_handoff.json", snapshot)
    # Stop the shell/Python coordinators, not their media-producing children.
    for p in snapshot["controllers"]:
        if alive(p["pid"]):
            os.kill(p["pid"], signal.SIGTERM)
    for name in ("wy_repair_chain2.state", "wy_repair_chain2b.state"):
        path = manager.legacy / name
        text = path.read_text() if path.is_file() else ""
        (manager.directory / name).write_text(text)
        for line in text.splitlines():
            if line.startswith("ep:"):
                manager.state["passes"][line[3:]] = 1
    batches = [last_batch(manager.legacy / name) for name in ("wy_repair_chain2.log", "wy_repair_chain2b.log")]
    adopted_batches = set()
    for p in snapshot["workers"]:
        args, pid = p["args"], p["pid"]
        if str(manager.legacy / "wy_verify_clips.py") in args:
            mode = args[args.index(str(manager.legacy / "wy_verify_clips.py")) + 1]
            if mode == "all":
                environ = Path(f"/proc/{pid}/environ")
                env = dict(s.split("=", 1) for s in environ.read_bytes().decode().split("\0") if "=" in s) if environ.exists() else {}
                source = "flash" if "flash" in env.get("VERIFY_OUT", "") else "local"
                manager.add("scan", [], pid=pid, source=source, adopted=True,
                         log=str(manager.legacy / ("wy_verify_flash_all.log" if source == "flash" else "wy_verify_all.log")))
                if source == "flash":
                    manager.state["flash_env"] = {k: env[k] for k in ("QWEN38_LOCAL_BASE_URL", "QWEN38_LOCAL_MODEL", "QWEN38_LOCAL_API_KEY_VAR", "QWEN38_LOCAL_STREAM") if k in env}
            else:
                manager.add("drain", [], pid=pid, adopted=True)
            continue
        episodes = episode_numbers(argument(args, "--chapters") or argument(args, "--episodes")) if "1-2043" not in args else []
        lane = next((i for i, (ns, _) in enumerate(batches) if ns and set(ns) == set(episodes)), None)
        is_gate = str(manager.legacy / "wy_gate.py") in args
        if is_gate:
            # The separate gate is redundant; already-flushed precise results are imported below.
            if alive(pid):
                os.kill(pid, signal.SIGTERM)
            if lane is not None and lane not in adopted_batches:
                manager.add("repair", episodes, step=legacy_step(args, batches[lane][1]), adopted=True)
                adopted_batches.add(lane)
        elif lane is not None and lane not in adopted_batches:
            manager.add("repair", episodes, step=legacy_step(args, batches[lane][1]), pid=pid if alive(pid) else None, adopted=True)
            adopted_batches.add(lane)
        else:
            manager.add("fill", episodes, step=1 if "--review-only" in args else 0, pid=pid if alive(pid) else None, adopted=True)
    for lane, (episodes, tail) in enumerate(batches):
        if lane in adopted_batches or not episodes:
            continue
        if "BATCH RESULT" in tail or "after retake 2:" in tail:
            for n in episodes:
                manager.state["passes"][str(n)] = 1
        else:
            step = 7 if "after retake 1:" in tail else 4 if "after repair:" in tail else 1 if "before repair:" in tail else 0
            manager.add("repair", episodes, step=step, adopted=True)
    manager.state["scan_started"] = any(j["kind"] == "scan" for j in manager.state["jobs"])
    manager.state["adopted_at"] = time.strftime("%F %T")
    manager.save()


def command(manager, job: dict) -> tuple[list[str], dict]:
    from novel_manga.util import load_dotenv
    from thin_profile import reference_image_env
    load_dotenv(ROOT / ".env")
    env = dict(os.environ, PYTHONPATH="src:scripts", NOVEL_VIDEO_MODEL="minimax-h3-ref2va-turbo",
               NOVEL_LOCAL_H3_URL="pool", NOVEL_INFLIGHT_POOL="h3pool", NOVEL_REVIEW_MODE="verify",
               NOVEL_INFLIGHT_DIR=str(manager.legacy.parent / "inflight/h3pool"), NOVEL_CLIP_SECONDS_MAX="15", PHANROUTER_VIDEO_KEY_VAR="")
    env.update(reference_image_env(env))
    python = str(ROOT / ".venv/bin/python")
    episodes = ",".join(map(str, job["episodes"]))
    if job["kind"] == "scan":
        source = job["source"]
        if source in {"shared_qwen", "shared_flash"}:
            lane = source.removeprefix('shared_')
            if lane == 'flash':
                env.update(manager.state.get('flash_env') or {})
            return [python, 'scripts/shared_audit_thin.py', '--novel-dir', str(manager.novel), '--state-dir', str(manager.directory),
                    '--queue', str(manager.directory / 'shared_audit.sqlite3'), '--lane', lane,
                    '--workers', '2' if lane == 'flash' else '8'], env
        output = manager.directory / f"scan_{source}.jsonl"
        prior = manager.legacy / ("wy_verify_flash.jsonl" if source == "flash" else "wy_verify.jsonl")
        if not output.exists() and prior.is_file():
            shutil.copy2(prior, output)  # resume a scanner from its written take records
        if source == "flash":
            env.update(manager.state.get("flash_env") or {})
        return [python, "scripts/verify_clips_thin.py", "--novel-dir", str(manager.novel), "--mode", "all", "--workers",
                "2" if source == "flash" else "8", "--judge-tag", source, "--out", str(output)], env
    step = schedule_rules.FLOWS[job["kind"]][job["step"]]
    if job['kind']=='repair' and step in {'repair','note1','note2'} and len(job['episodes'])==1:
        n=job['episodes'][0]
        return [python,'scripts/prepare_recovery_thin.py','--episode-dir',str(manager.novel/f'{manager.novel.name}_{n}'),
                '--kind','managed','--job-id',f"{job['id']}-step{job['step']}"],env
    if step == "recover":
        n = job["episodes"][0]
        return [python, "scripts/prepare_recovery_thin.py", "--episode-dir", str(manager.novel / f"{manager.novel.name}_{n}"),
                "--kind", job["recovery_kind"], "--job-id", job["id"]] + (
                    ['--extra-takes', str(job['extra_takes'])] if job.get('extra_takes') else []), env
    workers = "1" if len(job["episodes"]) == 1 else "6"
    if step in {"check", "review", "review1", "review2", "audit", "confirm"}:
        scope = "all" if job["kind"] == "recovery" else {"check": "candidates", "audit": "all", "confirm": "flash"}.get(step, "changed")
        return [python, "scripts/repair_review_thin.py", "--novel-dir", str(manager.novel), "--episodes", episodes,
                "--scope", scope, "--state-dir", str(manager.directory), "--legacy-dir", str(manager.legacy), "--workers", workers] + (
                ['--max-tokens', str(job['review_tokens'])] if job.get('review_tokens') else []), env
    if step == "repair":
        return [python, "scripts/repair_clips_thin.py", "--novel-dir", str(manager.novel), "--episodes", episodes, "--workers", workers, "--apply"], env
    if step.startswith("note"):
        return [python, "scripts/apply_review_feedback.py", "--novel-dir", str(manager.novel), "--episodes", episodes, "--must-fix", "--apply"], env
    return [python, "scripts/thin_batch.py", "--novel-dir", str(manager.novel), "--chapters", episodes,
            "--stage", "render", "--tier", "fast", "--merge", "1", "--parallel", "1" if len(job["episodes"]) == 1 else "12", "--workers", "0",
            "--card-parallel", "1" if len(job["episodes"]) == 1 else "6", "--no-recurring-cards",
            "--inflight", "24", "--plan-mode", "15", "--no-prescreen", "--prune"] + (
                ["--rerender"] if job["kind"] == "recovery" and job.get("recovery_kind") == "technical" else []) + (
                ["--cache-only"] if job.get("cache_only") else []), env


def finish(manager, job: dict):
    if job["kind"] == "recovery":
        # A recovery cycle finishing is not a claim that the episode passed.
        job["outcome"] = "awaiting_current_delivery_check"
    if job["kind"] == "repair" and job["step"] > 1:
        for n in job["episodes"]:
            manager.state["passes"][str(n)] = max(job["cycle"], manager.state["passes"].get(str(n), 0))
    if job["kind"] == "fill":
        for n in job["episodes"]:
            try:
                ready = thin_runs.episode_status(manager.novel / f"{manager.novel.name}_{n}", True) in {"done", "done_with_warnings"}
            except (OSError, ValueError, KeyError):
                ready = False
            manager.state["fill_failures"][str(n)] = 0 if ready else manager.state["fill_failures"].get(str(n), 0) + 1
    job.update(status="done", pid=None, finished_at=time.strftime("%F %T"))


def reap(manager):
    changed = False
    for job in manager.state["jobs"]:
        if job["status"] == "waiting_plan":
            if not any(clip_readiness.current_blocks(manager.novel / f"{manager.novel.name}_{n}") for n in job["episodes"]):
                job.update(status="pending", failures=0)
                job.pop("blocked_clips", None)
                changed = True
            continue
        if job["status"] != "running":
            continue
        pid = job["pid"]
        child = manager.children.get(pid)
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
        if job["kind"] in schedule_rules.FLOWS and schedule_rules.FLOWS[job["kind"]][job["step"]].startswith("render"):
            blocked = {str(n): rows for n in job["episodes"]
                       if (rows := clip_readiness.current_blocks(manager.novel / f"{manager.novel.name}_{n}"))}
            if blocked and len(job["episodes"]) == 1:
                job.update(status="waiting_plan", blocked_clips=blocked, failures=0)
                continue
            if code == 0 and len(job["episodes"]) == 1:
                n = job["episodes"][0]
                if thin_runs.episode_status(manager.novel / f"{manager.novel.name}_{n}", True) not in {"done", "done_with_warnings"}:
                    code = 1  # a batch CLI exiting normally does not prove that its media finished
        if code == 4 and (job["kind"] == "recovery" or (job['kind'] == 'scan' and str(job.get('source', '')).startswith('shared_'))):
            job.update(status="needs_attention", last_exit=code)
            continue
        if code != 0:
            job["failures"] += 1
            job.update(status="pending" if job["failures"] < 3 else "held", last_exit=code)
            continue
        job["failures"] = 0
        managed_preparation = (job['kind']=='repair' and schedule_rules.FLOWS['repair'][job['step']] in {'repair','note1','note2'}
                               or job['kind']=='recovery' and job['step']==0 and job.get('recovery_kind') in {'managed','residual','references','source','entities'})
        if managed_preparation and len(job['episodes'])==1:
            n=job['episodes'][0]
            preparation_id=f"{job['id']}-step{job['step']}" if job['kind']=='repair' else job['id']
            preparation=read(manager.novel/f'{manager.novel.name}_{n}'/'repair_history'/f'preparation-{preparation_id}.json',{})
            if preparation.get('skip_render'):
                finish(manager, job)
                continue
        if job["kind"] in {"scan", "drain"}:
            finish(manager, job)
            continue
        job["step"] += 1
        if job["step"] >= len(schedule_rules.FLOWS[job["kind"]]):
            finish(manager, job)
        else:
            job["status"] = "pending"
    return changed


def launch(manager):
    for job in manager.state["jobs"]:
        if job["status"] != "pending":
            continue
        # Existing legacy batches can overlap during adoption. Let their old
        # child finish, then serialize the next step on every claimed episode.
        if any(other["status"] == "running" and set(job["episodes"]) & set(other["episodes"]) for other in manager.state["jobs"]):
            continue
        blocked = {str(n): rows for n in job["episodes"]
                   if (rows := clip_readiness.current_blocks(manager.novel / f"{manager.novel.name}_{n}"))}
        if blocked and job["kind"] in {"repair", "fill", "recovery"} and schedule_rules.FLOWS[job["kind"]][job["step"]] != "recover":
            # An old batch can finish rendering while the new manager is
            # taking over. Its blocked episode resumes at generation after
            # its inputs change, not at a review of the old/missing video.
            renders = [i for i, step in enumerate(schedule_rules.FLOWS[job["kind"]]) if step.startswith("render") and i <= job["step"]]
            job.update(status="waiting_plan", blocked_clips=blocked, step=renders[-1] if renders else job["step"])
            continue
        if job["kind"] in {"repair", "fill", "recovery"} and schedule_rules.FLOWS[job["kind"]][job["step"]].startswith("review"):
            if any(manager.info.get(n, {}).get("status") in {"stale", "pending", "clips_failed", "plan_blocked"} for n in job["episodes"]):
                job["step"] = max(i for i, step in enumerate(schedule_rules.FLOWS[job["kind"]]) if step.startswith("render") and i < job["step"])
        if job["kind"] == "repair" and job["step"] in {1, 4, 7}:
            if all(not manager.info.get(n, {}).get("bad", 1) for n in job["episodes"]):
                finish(manager, job)
                continue
        slot = schedule_rules.stage_slots(job)
        if slot:
            used = sum(other_slot[1] for other in manager.state["jobs"] if other["status"] == "running"
                       and (other_slot := schedule_rules.stage_slots(other)) and other_slot[0] == slot[0])
            if used + slot[1] > schedule_rules.STAGE_CAPACITY[slot[0]]:
                continue
        argv, env = command(manager, job)
        log = manager.directory / f"{job['id']}-step{job['step']:02d}.log"
        job["launches"] = job.get("launches", 0) + 1
        result = manager.directory / f"{job['id']}-step{job['step']:02d}-run{job['launches']}.json"
        wrapped = [str(ROOT / ".venv/bin/python"), "scripts/run_repair_step.py", "--result", str(result), "--", *argv]
        with log.open("a") as stream:
            child = subprocess.Popen(wrapped, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                     stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        manager.children[child.pid] = child
        job.update(status="running", pid=child.pid, log=str(log), result=str(result), adopted=False, started_at=time.strftime("%F %T"))
        manager.save()
        print(f"{time.strftime('%H:%M:%S')} {job['id']} {job['kind']} step {job['step']} ({len(job['episodes'])} episodes)", flush=True)
