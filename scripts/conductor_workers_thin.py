"""conductor_workers_thin responsibilities; existing production limits and launch policy."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from thin_profile import reference_image_env
import os
import signal
import subprocess
import conductor_common_thin as conductor_common

def alive(conductor, name: str) -> bool:
    proc = conductor.procs.get(name)
    return proc is not None and proc.poll() is None


def external_running(conductor, pattern: str, mode: int | None = None) -> bool:
    """A process matching `pattern` that this conductor did not start.

    With `mode`, only a lane rendering that clip length counts; a lane that predates the
    --plan-mode flag carries none and counts for either, so restarts never duplicate it."""
    # "--" ends pgrep's own options: the patterns start with "--chapters".
    result = subprocess.run(["pgrep", "-f", "--", pattern], capture_output=True, text=True)
    if result.returncode != 0:
        return False
    own = {str(proc.pid) for proc in conductor.procs.values() if proc.poll() is None}
    for pid in (p.strip() for p in result.stdout.split()):
        if not pid or pid in own:
            continue
        if mode is None:
            return True
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ")
        except OSError:
            continue
        if "--plan-mode" not in cmdline or f"--plan-mode {mode}" in cmdline:
            return True
    return False


def spawn(conductor, name: str, command: list[str], extra_env: dict | None = None) -> None:
    if conductor.dry:
        conductor.log(f"[dry] would start {name}: {' '.join(command[:8])} ...")
        return
    env = {**os.environ, **conductor_common.BASE_ENV, **(extra_env or {})}
    env.update(reference_image_env(env))
    log_path = conductor.tmp / f"{name}.log"
    with log_path.open("ab") as handle:
        handle.write(f"\n===== {datetime.now():%F %T} {' '.join(command)}\n".encode())
        conductor.procs[name] = subprocess.Popen(command, cwd=conductor_common.REPO, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, start_new_session=True)
    conductor.log(f"started {name} (pid {conductor.procs[name].pid})")


def stop(conductor, name: str, why: str) -> None:
    proc = conductor.procs.get(name)
    if proc is None or proc.poll() is not None:
        return
    if conductor.dry:
        conductor.log(f"[dry] would stop {name}: {why}")
        return
    os.killpg(proc.pid, signal.SIGTERM)
    conductor.log(f"stopped {name}: {why}")


def key_env(conductor, key: dict) -> dict:
    env = {"NOVEL_VIDEO_MODEL": key["model"], "PHANROUTER_VIDEO_KEY_VAR": key["key_var"]}
    if key.get("pool"):
        env["NOVEL_INFLIGHT_POOL"] = key["pool"]
    if key.get("inflight_dir"):
        env["NOVEL_INFLIGHT_DIR"] = key["inflight_dir"]
    if key.get("base_url"):  # a key that names an instance renders locally, not through PhanRouter
        env["NOVEL_LOCAL_H3_URL"] = key["base_url"]
    if int(key["clip_cap"]) <= 15:
        env["NOVEL_CLIP_SECONDS_MAX"] = "15"
    return env


def server_env(conductor, server: dict) -> dict:
    env = {"QWEN38_LOCAL_BASE_URL": server["base"], "QWEN38_LOCAL_MODEL": server["model"],
           "NOVEL_LLM_BASE_URL": server["base"].split(",")[0], "NOVEL_LLM_MODEL": server["model"]}
    env["QWEN38_LOCAL_API_KEY_VAR"] = server.get("key_var", "")  # named, never the key itself
    return env
