"""A pool of local MiniMax-H3 instances that clips are handed to one at a time.

Each local instance used to be a key of its own - one lane, one fixed range of chapters - so when
an instance went away, or a faster one came, the ranges had to be cut again by hand.  The pool
turns that inside out: a clip asks for a free slot at the moment it is submitted, on whichever
instance is up and has room.  Lanes stop caring which machine renders them, and an instance
starts taking work as soon as it appears.

Members come from two places:

- the resident instances listed in configs/h3_pool.json, and
- whatever the H3 night shift (/mnt/disk1/zengzhitao/h3-night-shift) has leased and marked
  ``active`` in its controller's tick.json, rewritten every minute.  A night instance stops
  taking new clips ``drain_minutes`` before its lease deadline, so the clips it holds finish and
  are downloaded before the night shift hands the GPUs back.

An instance renders one job at a time and queues the rest, so it gets a couple of slots - lock
files under ``slot_dir/<host>_<port>/``, held with flock while a clip is on it.  One that stops
answering, or sits on a clip far longer than a clip takes, gets a cooldown file beside its slots
that every runner honours.  The pool file is read again as it changes: adding or removing an
instance needs no restart.
"""
from __future__ import annotations

import fcntl
import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CONFIG_ENV = "NOVEL_H3_POOL_CONFIG"
DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "h3_pool.json"
HEALTH_SECONDS = 60.0  # one probe per instance per minute, shared by every runner
MEMBERS_SECONDS = 10.0  # how long one process reuses its reading of the pool file and tick.json


def instance_key(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.hostname}_{parsed.port or 80}"


@dataclass(frozen=True)
class Instance:
    url: str
    name: str
    slots: int
    source: str  # "resident", or "night:<lease id>"
    drain_at: float | None = None  # a night instance takes no new clip after this

    @property
    def key(self) -> str:
        return instance_key(self.url)


def night_leases(state: dict) -> list[dict]:
    """Every lease in a night-shift controller result.  A machine's record holds them under
    'recovery' (the host's own restore pass), 'agent' (a status call) or 'action' (this tick's
    claims), oldest first, so a later record of the same lease wins."""
    leases: dict[str, dict] = {}
    for machine in state.get("machines") or []:
        for part in ("recovery", "agent", "action"):
            for lease in (machine.get(part) or {}).get("leases") or []:
                if lease.get("id"):
                    leases[str(lease["id"])] = lease
    return list(leases.values())


def night_instances(night: dict, excluded: set, now: float, default_slots: int) -> list[Instance]:
    path = Path(night["state"])
    try:
        age = now - path.stat().st_mtime
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    # The controller rewrites tick.json every minute.  A file that has stopped changing means it is
    # not running, and each host hands its GPUs back on its own schedule without the file saying so.
    if age > float(night.get("max_state_age_seconds", 600)):
        return []
    drain = float(night.get("drain_minutes", 15)) * 60
    found = []
    for lease in night_leases(state):
        deadline = float(lease.get("deadline") or 0)
        # starting is not ready yet, restoring and restored take no new work, and a lease whose end
        # is unknown or near keeps what it holds but gets nothing new
        if lease.get("phase") != "active" or not deadline or now >= deadline - drain:
            continue
        for item in lease.get("instances") or []:
            url = str(item.get("url") or "").rstrip("/")
            if url and urllib.parse.urlparse(url).hostname not in excluded:
                found.append(Instance(url, f"夜班 {item.get('unit') or lease['id']}",
                                      int(night.get("slots", default_slots)), f"night:{lease['id']}", deadline - drain))
    return found


def release(handle) -> None:
    if handle is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()


class H3Pool:
    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path or os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG)
        self._members: tuple[float, list[Instance]] = (float("-inf"), [])
        self._lock = threading.Lock()

    def config(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    @property
    def slot_dir(self) -> Path:
        return Path(self.config().get("slot_dir", "/mnt/disk1/zengzhitao/tmp/inflight/h3pool-instances"))

    @property
    def stuck_seconds(self) -> float:
        return float(self.config().get("stuck_minutes", 15)) * 60

    @property
    def stuck_cooldown(self) -> float:
        return float(self.config().get("stuck_cooldown_minutes", 20)) * 60

    @property
    def unreachable_cooldown(self) -> float:
        return float(self.config().get("unreachable_cooldown_minutes", 5)) * 60

    # ------------------------------------------------------------ members
    def members(self, now: float | None = None) -> list[Instance]:
        """Every instance that may take a new clip: the enabled residents, and the night shift's
        active leases that are not yet draining.  Hosts in exclude_hosts are left out of both."""
        now = time.time() if now is None else now
        with self._lock:
            at, cached = self._members
            if 0 <= now - at < MEMBERS_SECONDS:
                return cached
        cfg = self.config()
        excluded = set(cfg.get("exclude_hosts") or [])
        slots = int(cfg.get("slots", 2))
        found = []
        for entry in cfg.get("resident") or []:
            url = str(entry.get("url") or "").rstrip("/")
            if url and entry.get("enabled", True) and urllib.parse.urlparse(url).hostname not in excluded:
                found.append(Instance(url, entry.get("name") or url, int(entry.get("slots", slots)), "resident"))
        night = cfg.get("night_shift") or {}
        if night.get("state"):
            found += night_instances(night, excluded, now, slots)
        unique: dict[str, Instance] = {}
        for instance in found:
            unique.setdefault(instance.url, instance)
        result = list(unique.values())
        with self._lock:
            self._members = (now, result)
        return result

    def excluded(self, url: str) -> bool:
        """An instance taken out of the pool: its host is excluded or its resident entry is off.  A
        clip still sitting on one is rendered again elsewhere rather than waited for."""
        cfg = self.config()
        off = {str(e.get("url") or "").rstrip("/") for e in cfg.get("resident") or [] if not e.get("enabled", True)}
        return urllib.parse.urlparse(url).hostname in set(cfg.get("exclude_hosts") or []) or url.rstrip("/") in off

    # ------------------------------------------------------------ health
    def _dir(self, url: str) -> Path:
        directory = self.slot_dir / instance_key(url)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def cooling(self, url: str, now: float | None = None) -> bool:
        try:
            until = float((self.slot_dir / instance_key(url) / "cooldown_until").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False
        return until > (time.time() if now is None else now)

    def cool_down(self, url: str, seconds: float, reason: str) -> None:
        directory = self._dir(url)
        temp = directory / f".cooldown.{os.getpid()}.{threading.get_ident()}"
        temp.write_text(str(time.time() + seconds), encoding="utf-8")
        os.replace(temp, directory / "cooldown_until")
        with open(directory / "events.log", "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} cooldown {seconds / 60:.0f} min: {reason}\n")

    def healthy(self, url: str) -> bool:
        """Whether the instance answers - asked about once a minute in all, the answer shared by
        every runner through a file beside its slots."""
        cache = self._dir(url) / "health.json"
        try:
            record = json.loads(cache.read_text(encoding="utf-8"))
            if 0 <= time.time() - float(record["at"]) < HEALTH_SECONDS:
                return bool(record["ok"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        ok = False
        for path in ("/health", "/v1/videos?limit=1&order=desc"):
            try:
                with urllib.request.urlopen(url + path, timeout=5) as response:
                    ok = response.status == 200
                break
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    break  # it answered, badly
            except (urllib.error.URLError, OSError, ValueError):
                break
        temp = cache.with_name(f".health.{os.getpid()}.{threading.get_ident()}")
        temp.write_text(json.dumps({"at": time.time(), "ok": ok}), encoding="utf-8")
        os.replace(temp, cache)
        return ok

    # ------------------------------------------------------------ slots
    def acquire(self, timeout: float | None = None) -> tuple[Instance, object]:
        """Block until some member has a free slot, and return it with the slot's lock held."""
        started = time.monotonic()
        while True:
            now = time.time()
            members = [m for m in self.members(now) if not self.cooling(m.url, now)]
            random.shuffle(members)  # no instance is favoured for being listed first
            for instance in members:
                directory = self._dir(instance.url)
                for index in range(instance.slots):
                    handle = open(directory / f"slot_{index:02d}.lock", "w")
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError:
                        handle.close()
                        continue
                    if self.healthy(instance.url):
                        return instance, handle
                    release(handle)
                    break  # down: the next instance
            if timeout is not None and time.monotonic() - started >= timeout:
                raise TimeoutError("no H3 pool instance has a free slot")
            time.sleep(0.05 if timeout is not None else 3.0)
