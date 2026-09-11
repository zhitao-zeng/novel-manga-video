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
files under ``slot_dir/<host>_<port>/``, held with flock while a clip is on it.

Whether an instance may take work is judged twice over.  It must answer; and, when the night
shift has inspected its host in the last few minutes - its controller runs nvidia-smi on every
host once a minute and records, per GPU, the utilization and the service holding it - its service
must hold at least one GPU, and those GPUs must not have sat idle for three samples while it holds
jobs.  The first catches a stopped instance; the second one whose front end still answers and
takes jobs after its GPU worker is gone (GPU003-B, 2026-09-11: an hour of jobs accepted, none
rendered, while /health said fine).  A failing instance gets a cooldown file beside its slots that
every runner honours.  The pool file is read again as it changes: no restart to add or remove one.
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
HEALTH_SECONDS = 60.0  # one judgement per instance per minute, shared by every runner
MEMBERS_SECONDS = 10.0  # how long one process reuses its reading of the pool file and tick.json
WAITING = {"queued", "running", "in_progress", "processing"}  # it reports the job it renders as queued


def instance_key(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.hostname}_{parsed.port or 80}"


@dataclass(frozen=True)
class Instance:
    url: str
    name: str
    slots: int
    source: str  # "resident", "night:<lease id>" or "unknown"
    drain_at: float | None = None  # a night instance takes no new clip after this
    host: str | None = None  # machine id in the night shift's inspection: gpu52, gpu03, gpu81, local
    service: str | None = None  # the systemd unit whose processes hold its GPUs

    @property
    def key(self) -> str:
        return instance_key(self.url)


def night_leases(state: dict) -> list[dict]:
    """Every lease in a night-shift controller result, each tagged with its machine.  A machine's
    record holds them under 'recovery' (the host's own restore pass), 'agent' (a status call) or
    'action' (this tick's claims), oldest first, so a later record of the same lease wins."""
    leases: dict[str, dict] = {}
    for machine in state.get("machines") or []:
        for part in ("recovery", "agent", "action"):
            for lease in (machine.get(part) or {}).get("leases") or []:
                if lease.get("id"):
                    leases[str(lease["id"])] = {**lease, "_machine": machine.get("machine_id")}
    return list(leases.values())


def night_instances(state: dict, night: dict, excluded: set, now: float, default_slots: int) -> list[Instance]:
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
            unit = item.get("unit")
            if url and urllib.parse.urlparse(url).hostname not in excluded:
                found.append(Instance(url, f"夜班 {unit or lease['id']}", int(night.get("slots", default_slots)),
                                      f"night:{lease['id']}", deadline - drain,
                                      host=lease.get("_machine"), service=f"{unit}.service" if unit else None))
    return found


def gpu_view(state: dict) -> tuple[dict, set]:
    """(machine id, systemd unit) -> utilization of every GPU the unit holds, from the night shift's
    last inspection, and the set of machines it inspected: a host it could not inspect is unknown,
    not a host whose services hold nothing."""
    view: dict[tuple, list[float]] = {}
    inspected = set()
    for machine in state.get("machines") or []:
        gpus = (machine.get("inspection") or {}).get("gpus")
        if not gpus:
            continue
        inspected.add(machine.get("machine_id"))
        for gpu in gpus:
            for service in gpu.get("services") or []:
                unit = str(service).split(":", 1)[-1]  # "systemd:zzt-h3-r2v-a.service"
                view.setdefault((machine.get("machine_id"), unit), []).append(float(gpu.get("utilization") or 0))
    return view, inspected


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
        self._night: tuple[float, dict | None, float] = (float("-inf"), None, 0.0)
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
    def night_state(self, now: float | None = None) -> tuple[dict | None, float]:
        """The night shift's tick.json and its age in seconds (read at most every MEMBERS_SECONDS)."""
        path = (self.config().get("night_shift") or {}).get("state")
        if not path:
            return None, float("inf")
        now = time.time() if now is None else now
        with self._lock:
            at, state, mtime = self._night
            if state is not None and 0 <= now - at < MEMBERS_SECONDS:
                return state, now - mtime
        try:
            mtime = Path(path).stat().st_mtime
            state = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, float("inf")
        with self._lock:
            self._night = (now, state, mtime)
        return state, now - mtime

    def _fresh_night(self, now: float | None = None) -> dict | None:
        """tick.json if the controller is still writing it.  A file that has stopped changing means
        it is not running, and each host hands its GPUs back on its own schedule without saying so."""
        state, age = self.night_state(now)
        limit = float((self.config().get("night_shift") or {}).get("max_state_age_seconds", 600))
        return state if state is not None and age <= limit else None

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
                found.append(Instance(url, entry.get("name") or url, int(entry.get("slots", slots)), "resident",
                                      host=entry.get("host"), service=entry.get("service")))
        night = cfg.get("night_shift") or {}
        state = self._fresh_night(now) if night.get("state") else None
        if state is not None:
            found += night_instances(state, night, excluded, now, slots)
        unique: dict[str, Instance] = {}
        for instance in found:
            unique.setdefault(instance.url, instance)
        result = list(unique.values())
        with self._lock:
            self._members = (now, result)
        return result

    def lookup(self, url: str) -> Instance:
        """The instance behind a URL - a member, a switched-off resident, or a night lease that is
        draining - so a clip already on it can still be judged."""
        url = url.rstrip("/")
        for member in self.members():
            if member.url == url:
                return member
        cfg = self.config()
        for entry in cfg.get("resident") or []:
            if str(entry.get("url") or "").rstrip("/") == url:
                return Instance(url, entry.get("name") or url, int(entry.get("slots", cfg.get("slots", 2))), "resident",
                                host=entry.get("host"), service=entry.get("service"))
        for lease in night_leases(self._fresh_night() or {}):
            for item in lease.get("instances") or []:
                if str(item.get("url") or "").rstrip("/") == url:
                    unit = item.get("unit")
                    return Instance(url, f"夜班 {unit or lease['id']}", 2, f"night:{lease['id']}",
                                    host=lease.get("_machine"), service=f"{unit}.service" if unit else None)
        return Instance(url, url, 2, "unknown")

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

    def _reachable(self, url: str) -> bool:
        for path in ("/health", "/v1/videos?limit=1&order=desc"):
            try:
                with urllib.request.urlopen(url + path, timeout=5) as response:
                    return response.status == 200
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    return False  # it answered, badly
            except (urllib.error.URLError, OSError, ValueError):
                return False
        return False

    def _waiting(self, url: str) -> int:
        try:
            with urllib.request.urlopen(url + "/v1/videos?limit=100&order=desc", timeout=10) as response:
                jobs = json.loads(response.read()).get("data", [])
        except (urllib.error.URLError, OSError, ValueError):
            return 0
        return sum(1 for job in jobs if str(job.get("status", "")).lower() in WAITING)

    def gpu_verdict(self, instance: Instance, now: float | None = None) -> str | None:
        """What the night shift's last nvidia-smi pass says about the GPUs behind this instance:
        None when it cannot tell (no service named, tick.json stale, host not inspected); 'down'
        when the service holds no GPU; otherwise the busiest GPU's utilization joins the instance's
        history, and 'idle' comes back once the last idle_samples readings, a minute apart, were all
        at or under idle_percent - 'busy' until then."""
        if not (instance.host and instance.service):
            return None
        state = self._fresh_night(now)
        if state is None:
            return None
        view, inspected = gpu_view(state)
        if instance.host not in inspected:
            return None
        utilization = view.get((instance.host, instance.service))
        if not utilization:
            return "down"
        path = self._dir(instance.url) / "gpu_history.json"
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            history = []
        sample = [float(state.get("time") or 0), max(utilization)]
        if not history or history[-1][0] != sample[0]:
            history = (history + [sample])[-10:]
            temp = path.with_name(f".gpu_history.{os.getpid()}.{threading.get_ident()}")
            temp.write_text(json.dumps(history), encoding="utf-8")
            os.replace(temp, path)
        check = self.config().get("gpu_check") or {}
        quiet, needed = float(check.get("idle_percent", 5)), int(check.get("idle_samples", 3))
        recent = history[-needed:]
        if len(recent) == needed and all(u <= quiet for _, u in recent):
            span = recent[-1][0] - recent[0][0]
            if 60 * (needed - 1) - 15 <= span <= 60 * (needed + 2):
                return "idle"
        return "busy"

    def problem(self, target: Instance | str) -> str | None:
        """Why this instance should get no clip now, or None if it may.  Judged at most once a
        minute and shared by every runner through health.json beside its slots; a problem also puts
        the instance on a cooldown."""
        url = (target.url if isinstance(target, Instance) else str(target)).rstrip("/")
        cache = self._dir(url) / "health.json"
        try:
            record = json.loads(cache.read_text(encoding="utf-8"))
            if 0 <= time.time() - float(record["at"]) < HEALTH_SECONDS:
                return record.get("why")
        except (OSError, ValueError, KeyError, TypeError):
            pass
        instance = target if isinstance(target, Instance) else self.lookup(url)
        why, cooldown = None, 0.0
        if not self._reachable(url):
            why, cooldown = "does not answer", self.unreachable_cooldown
        else:
            verdict = self.gpu_verdict(instance)
            if verdict == "down":
                why, cooldown = f"nvidia-smi on {instance.host}: {instance.service} holds no GPU", self.unreachable_cooldown
            elif verdict == "idle" and self._waiting(url):
                why, cooldown = f"nvidia-smi on {instance.host}: GPUs idle for three samples with jobs waiting", self.stuck_cooldown
        temp = cache.with_name(f".health.{os.getpid()}.{threading.get_ident()}")
        temp.write_text(json.dumps({"at": time.time(), "ok": why is None, "why": why}), encoding="utf-8")
        os.replace(temp, cache)
        if why:
            self.cool_down(url, cooldown, why)
        return why

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
                    if self.problem(instance) is None:
                        return instance, handle
                    release(handle)
                    break  # not fit: the next instance
            self._pause(started, timeout, "no H3 pool instance has a free slot")

    def hold(self, url: str, timeout: float | None = None):
        """A slot on one particular instance - the one a resumed task already runs on - waiting while its
        slots are busy.  Taken whatever the instance's state: a draining night instance still finishes the
        clips it has, and the caller's polling judges its health."""
        instance = self.lookup(url)
        directory = self._dir(instance.url)
        started = time.monotonic()
        while True:
            for index in range(instance.slots):
                handle = open(directory / f"slot_{index:02d}.lock", "w")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return handle
                except OSError:
                    handle.close()
            self._pause(started, timeout, f"no free slot on {instance.name} for the task it already holds")

    @staticmethod
    def _pause(started: float, timeout: float | None, message: str) -> None:
        """Wait before looking again, or raise TimeoutError once `timeout` has passed.  A short wait (a test)
        looks often; a clip waiting out a busy pool looks every few seconds, not twenty times a second in
        every waiting runner."""
        if timeout is None:
            time.sleep(3.0)
            return
        left = timeout - (time.monotonic() - started)
        if left <= 0:
            raise TimeoutError(message)
        time.sleep(min(left, 0.05 if timeout <= 5 else 3.0))
