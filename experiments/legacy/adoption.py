"""Historical shell-to-manager handoff, retained for experiment replay only."""
from pathlib import Path
import os
import signal
import time
from novel_manga.util import atomic_write_json
from repair_manager_workers_thin import ROOT, alive, processes

CONTROLLERS = {"wy_repair_chain2.sh", "wy_repair_chain2b.sh", "wy_repair_pass2.sh", "wy_filler.sh",
               "wy_verify_run2.sh", "wy_verify_flash_all.sh", "wy_single_card_recovery.py"}


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

