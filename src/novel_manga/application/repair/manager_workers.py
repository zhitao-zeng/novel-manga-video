"""repair_manager_workers_thin responsibilities; existing job state and scheduling policy."""
from __future__ import annotations
from novel_manga.application.configuration import project_root
from novel_manga.util import atomic_write_json
from novel_manga.util import read_json as read
from pathlib import Path
import os
import shutil
import signal
import subprocess
import time
ROOT = project_root()
import novel_manga.application.preparation.readiness as clip_readiness
import novel_manga.repair.scheduling as schedule_rules
import novel_manga.application.production.runs as thin_runs



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














def command(manager, job: dict) -> tuple[list[str], dict]:
    from novel_manga.application.configuration import repair_environment
    from novel_manga.application.profiles import reference_image_env
    env, options = repair_environment(ROOT, manager.legacy)
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
            "--inflight", str(options["inflight"]), "--plan-mode", str(options["clip_cap"]), "--no-prescreen", "--prune"] + (
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
