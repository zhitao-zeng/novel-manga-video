"""conductor_capacity_thin responsibilities; existing production limits and launch policy."""
from __future__ import annotations
from pathlib import Path
import fcntl
import os
import time
import novel_manga.application.production.conductor_state as conductor_state

def compatible(conductor, key: dict, r: dict) -> bool:
    return r["plan_mode"] == int(key["clip_cap"])


def _initial_limit(conductor, key: dict) -> int:
    """Resume the AIMD where the last conductor left it: a restart is not a throttle.
    Falls back to the configured start when the pool has no limit file yet."""
    lo, hi = int(key["inflight"]["min"]), int(key["inflight"]["max"])
    return max(lo, min(hi, read_limit(conductor, key, int(key["inflight"]["start"]))))


def pool_dir(conductor, key: dict) -> Path:
    if key.get("inflight_dir"):
        return Path(key["inflight_dir"])
    return conductor.novel_dir / (f".inflight-{key['pool']}" if key.get("pool") else ".inflight")


def owns_limit(conductor, key: dict) -> bool:
    """One conductor drives the AIMD on a shared pool; the others follow what they read.
    Ownership is a pid beside the limit and passes on when that process is gone."""
    if not key.get("inflight_dir"):
        return True
    path = pool_dir(conductor, key) / "limit.owner"
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = 0
    if pid == os.getpid():
        return True
    if pid > 0:
        try:
            os.kill(pid, 0)
            return False
        except (OSError, ProcessLookupError):
            pass
    if not conductor.dry:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(os.getpid()), encoding="utf-8")
        conductor.log(f"{key['name']}: taking the in-flight limit for {pool_dir(conductor, key)}")
    return True


def read_limit(conductor, key: dict, fallback: int) -> int:
    try:
        return int((pool_dir(conductor, key) / "limit").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return fallback


def write_limit(conductor, key: dict, limit: int) -> None:
    directory = pool_dir(conductor, key)
    directory.mkdir(parents=True, exist_ok=True)
    if not conductor.dry:
        (directory / "limit").write_text(str(limit), encoding="utf-8")


def tick_aimd(conductor, name: str, key: dict, chapters: list[int]) -> None:
    lane = conductor.lanes[name]
    aimd = conductor.cfg.get("aimd", {})
    now = time.time()
    # Count only the lines since the last limit change: one burst of 429s halves the
    # limit once, instead of on every tick until the burst ages out of the window.
    window = int(min(aimd.get("window_seconds", 600), max(1.0, now - lane["limit_changed"])))
    quota = conductor_state.recent_lines(conductor, chapters, "quota_not_enough", aimd.get("window_seconds", 600)) if chapters else 0
    if quota:
        park = max(1800, int(aimd.get("park_seconds", 3600)))
        conductor.log(f"{name}: QUOTA EXHAUSTED - {quota} x HTTP 403 quota_not_enough; parking this key for {park} s "
                 "and stopping its lane (top the account up, then restart the conductor)")
        lane["parked_until"] = now + park
        return
    n429 = conductor_state.recent_lines(conductor, chapters, "HTTP 429", window) if chapters else 0
    lo, hi = int(key["inflight"]["min"]), int(key["inflight"]["max"])
    if n429 >= aimd.get("decrease_at", 5):
        new = max(lo, lane["limit"] // 2)
        if new != lane["limit"]:
            conductor.log(f"{name}: {n429} x 429 in the window; in-flight {lane['limit']} -> {new}")
            lane["limit"], lane["limit_changed"] = new, now
        lane["throttled_since"] = lane["throttled_since"] or now
        if lane["limit"] == lo and now - lane["throttled_since"] > aimd.get("park_after_seconds", 1800):
            lane["parked_until"] = now + aimd.get("park_seconds", 3600)
            lane["throttled_since"] = None
            conductor.log(f"{name}: throttled at the floor for too long; parked for {aimd.get('park_seconds', 3600)} s")
    else:
        if n429 == 0:
            lane["throttled_since"] = None
        if n429 == 0 and lane["limit"] < hi and now - lane["limit_changed"] >= aimd.get("increase_after_seconds", 600):
            lane["limit"] = min(hi, lane["limit"] + aimd.get("increase_step", 2))
            lane["limit_changed"] = now
            conductor.log(f"{name}: calm; in-flight -> {lane['limit']}")
    if owns_limit(conductor, key):
        write_limit(conductor, key, lane["limit"])
    else:  # another conductor drives this pool; follow it so this lane's view stays true
        lane["limit"] = max(int(key["inflight"]["min"]), min(int(key["inflight"]["max"]), read_limit(conductor, key, lane["limit"])))


def held_slots(conductor, key: dict) -> int:
    directory = pool_dir(conductor, key)
    held = 0
    for path in directory.glob("slot_*.lock"):
        try:
            with open(path, "r+") as handle:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle, fcntl.LOCK_UN)
                except OSError:
                    held += 1
        except OSError:
            pass
    return held
