#!/usr/bin/env python3
"""Conductor: one control loop for reading, planning, cards, rendering lanes and reviews.

It replaces the hand-run lane loops, relay scripts and review loop of the
101-1000 run with rules that read the state on disk every tick:

  * in-flight per video key follows AIMD on HTTP 429s (a `limit` file in the
    key's slot pool that the runner re-reads), and a key that stays throttled
    at the floor is parked until a later probe;
  * each key renders the next queued range whose plan type it can take
    (15 s plans on either key, 30 s plans only on a key with a 30 s cap); an
    idle key joins the other key's range, episode locks keep them apart;
  * planning blocks are cut back while Qwen is congested or the card gate is
    waiting, and grow again when it is calm; the reading pass runs ahead;
  * the card pre-pass runs for a range before and after its planning;
  * reviews run only while Qwen is idle.

Usage: conductor_thin.py --config configs/conductor.zhutian.json [--dry-run] [--once]
"""
from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from thin_runs import REVIEW_POLICY, RENDER_RUNS_PER_PLAN, episode_status, gate_failures, render_runs
from thin_profile import reference_image_env

REPO = Path(__file__).resolve().parent.parent
PY = str(REPO / ".venv" / "bin" / "python")
SCRIPTS = REPO / "scripts"


def load_dotenv(path: Path) -> None:
    """Read .env into the environment, as thin_batch.py does.  The conductor passes os.environ to every
    worker it spawns, and build_cards_thin.py has no reader of its own: without this, a conductor started
    from a shell that never sourced .env gives its card workers no PHANROUTER_API_KEY and they exit at
    once (2026-09-12: 514 cards queued, 0 built)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)
BASE_ENV = {"PYTHONPATH": "src:scripts", "NOVEL_PLANNER_BACKEND": "deterministic",
            "NOVEL_CREATIVE_PROFILE": "short-drama-adaptive-v1"}
PLAN_BLOCK_RUNS = 3  # runs a planning block gets while some of its chapters are left without a plan
PLAN_RETRY_SECONDS = 600  # ...spaced out, so a planning-server outage does not burn them in three ticks
REVIEW_ERROR_ROUNDS = 3  # reviews an episode gets while the judge keeps failing on some of its clips
REVIEW_BATCH_TRIES = 3  # review batches a final is put in before the conductor stops waiting for its review


class Conductor:
    def __init__(self, config: dict, dry_run: bool, plan_only: bool = False):
        self.cfg = config
        self.dry = dry_run
        self.plan_only = plan_only  # reading, planning and cards only: no rendering lanes
        self.novel_dir = (REPO / config["novel_dir"]).resolve()
        self.novel_id = self.novel_dir.name
        self.tmp = Path(config.get("tmp_dir", "/mnt/disk1/zengzhitao/tmp/conductor"))
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.procs: dict[str, subprocess.Popen] = {}
        self.keys = {k["name"]: k for k in config["keys"]}
        # Every key renders on local H3: a retake costs nothing, so a final with gate-failed clips goes back
        # to the lane (final_settled).  A paid lane leaves that decision to a person.
        self.free = bool(self.keys) and all(k.get("base_url") for k in self.keys.values())
        self._summaries: dict[tuple[str, str], tuple[float, object]] = {}
        self._readings: dict[str, tuple[tuple, tuple]] = {}
        self.review_tries: dict[int, tuple[float, int]] = {}  # chapter -> (final's mtime, review batches it was in)
        self.ranges = [self._parse_range(r) for r in config["ranges"]]
        self.lanes = {name: {"range": None, "next_round_at": 0.0, "parked_until": 0.0, "limit": self._initial_limit(k),
                             "limit_changed": time.time(), "throttled_since": None} for name, k in self.keys.items()}
        self.blocks: list[dict] = []  # planning blocks in queue order
        for r in self.ranges:
            size = config["planning"]["block_size"]
            for a in range(r["a"], r["b"] + 1, size):
                self.blocks.append({"a": a, "b": min(r["b"], a + size - 1), "mode": r["plan_mode"], "done": False, "proc": None, "started": 0.0, "server": None,
                                    "runs": 0, "retry_at": 0.0})
        self.prepassed: dict[str, int] = {}
        self.last_review = 0.0
        self.log_path = self.tmp / "conductor.log"

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _parse_range(r: dict) -> dict:
        a, b = (int(x) for x in str(r["chapters"]).split("-"))
        return {"a": a, "b": b, "plan_mode": int(r.get("plan_mode", 30)), "prepass_after_plan": False}

    def log(self, message: str) -> None:
        line = f"{datetime.now():%m-%d %H:%M:%S} {message}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def episode_dir(self, n: int) -> Path:
        return self.novel_dir / f"{self.novel_id}_{n}"

    def chapter(self, n: int) -> dict:
        d = self.episode_dir(n)
        state = {"n": n, "done": False, "blocked": False, "planned": (d / "clip_plan.json").is_file(), "runs": 0, "mode": None,
                 "unreviewed": False}
        if not state["planned"]:
            return state
        state["mode"], status, retakeable = self.reading(d)
        # The count thin_batch keeps, per plan and set of corrections.  Comparing the plan's mtime alone with the
        # pair it writes read 0 runs everywhere and kept given-up episodes renderable.
        state["runs"] = render_runs(d)
        # One reading with thin_batch (thin_runs.episode_status).  An episode with a video file is not done when its
        # plan, a correction or an English prompt changed since: counted as done, a range already rendered was never
        # scheduled again.  A preview (done_with_warnings) is not done either: a free lane takes it back while the
        # speech gate's failures can be retaken and runs remain; otherwise it waits for a person - "blocked", like an
        # episode that used up its runs - and is neither done nor work for a lane.
        if status == "done":
            state["done"] = True
        elif status == "done_with_warnings":
            state["blocked"] = not (self.free and retakeable and state["runs"] < RENDER_RUNS_PER_PLAN)
        elif state["runs"] >= RENDER_RUNS_PER_PLAN:
            state["blocked"] = True
        mp4 = d / f"{self.novel_id}_{n}.mp4"
        if status in {"done", "done_with_warnings"} and (state["done"] or state["blocked"]):
            final = mp4.stat().st_mtime
            review = d / "episode_review.json"
            seen, batches = self.review_tries.get(n, (final, 0))
            state["unreviewed"] = (not (seen == final and batches >= REVIEW_BATCH_TRIES)
                                   and (not review.is_file() or review.stat().st_mtime < final or self.review_incomplete(review)))
        return state

    def reading(self, d: Path) -> tuple[int | None, str, bool]:
        """(clip length of the plan, thin_runs.episode_status, whether the final has gate failures to retake), kept
        until one of the files it is read from changes: every tick looks at every episode."""
        key = tuple(self._mtime(d / name) for name in ("clip_plan.json", "thin_media_report.json", "review_feedback.json", f"{d.name}.mp4"))
        hit = self._readings.get(str(d))
        if hit and hit[0] == key:
            return hit[1]
        try:
            policy = json.loads((d / "clip_plan.json").read_text(encoding="utf-8")).get("policy", "")
            mode = 15 if "-15s" in policy else 30
        except (OSError, ValueError):
            mode = 30
        try:
            status = episode_status(d, self.free)
        except (OSError, ValueError, KeyError):
            status = "pending"
        value = (mode, status, bool(gate_failures(d)))
        self._readings[str(d)] = (key, value)
        return value

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def settled(self, n: int) -> bool:
        """Planned, or left out on purpose (planning_skipped.json: too short to be an episode)."""
        d = self.episode_dir(n)
        return (d / "clip_plan.json").is_file() or (d / "planning_skipped.json").is_file()

    def summary(self, path: Path, kind: str, summarize):
        """summarize(the file's JSON), kept until the file changes: every tick looks at every episode."""
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        hit = self._summaries.get((str(path), kind))
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            value = summarize(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, AttributeError, TypeError):
            value = None
        self._summaries[(str(path), kind)] = (mtime, value)
        return value

    def review_incomplete(self, review: Path) -> bool:
        """A review in which the judge failed on some clips (severity review_error) has not reviewed them: it
        goes back in the queue, for a few rounds.  Only the review file's age used to count, and 28 of 雾月's
        and 3 of 诸天's episodes sat as reviewed with those clips unjudged and unflagged (2026-09-11)."""
        errors = self.summary(review, "review_errors", lambda data: (
            sum(1 for c in (data.get("clips") or {}).values() if c.get("severity") == "review_error"),
            int(data.get("error_rounds", 0)), data.get("policy")))
        return errors is None or errors[2] != REVIEW_POLICY or (errors[0] > 0 and errors[1] < REVIEW_ERROR_ROUNDS)

    def range_stats(self, r: dict) -> dict:
        chapters = [self.chapter(n) for n in range(r["a"], r["b"] + 1)]
        renderable = [c["n"] for c in chapters if c["planned"] and not c["done"] and not c["blocked"]
                      and c["runs"] < RENDER_RUNS_PER_PLAN and c["mode"] == int(r.get("plan_mode", 30))]
        return {"total": len(chapters), "planned": sum(c["planned"] for c in chapters), "done": sum(c["done"] for c in chapters),
                "blocked": [c["n"] for c in chapters if c["blocked"]], "renderable": renderable,
                "unreviewed": [c["n"] for c in chapters if c["unreviewed"]]}

    def held_slots(self, key: dict) -> int:
        directory = self.pool_dir(key)
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

    def recent_lines(self, chapters, pattern: str, seconds: int) -> int:
        """Lines matching `pattern` in the chapters' render logs within the last `seconds` (same day)."""
        cutoff = (datetime.now() - timedelta(seconds=seconds)).strftime("%H:%M:%S")
        count = 0
        for n in chapters:
            path = self.episode_dir(n) / "render.log"
            if not path.is_file() or time.time() - path.stat().st_mtime > seconds + 60:
                continue
            try:
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
                    if len(line) > 9 and line[0] == "[" and line[1:9] >= cutoff and pattern in line:
                        count += 1
            except OSError:
                pass
        return count

    def qwen_waiting(self) -> int:
        total = 0
        for url in self.cfg["qwen"]["urls"]:
            base = url.rsplit("/v1", 1)[0]
            try:
                with urllib.request.urlopen(base + "/metrics", timeout=3) as response:
                    for line in response.read().decode("utf-8", "replace").splitlines():
                        if line.startswith("vllm:num_requests_waiting"):
                            total += int(float(line.rsplit(" ", 1)[-1]))
            except Exception:  # noqa: BLE001 - a dead endpoint counts as idle
                pass
        return total

    def read_upto(self) -> int:
        try:
            return max(int(k) for k in json.loads((self.novel_dir / "bible_growth.json").read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return 0

    # -------------------------------------------------------------- processes
    def alive(self, name: str) -> bool:
        proc = self.procs.get(name)
        return proc is not None and proc.poll() is None

    def external_running(self, pattern: str, mode: int | None = None) -> bool:
        """A process matching `pattern` that this conductor did not start.

        With `mode`, only a lane rendering that clip length counts; a lane that predates the
        --plan-mode flag carries none and counts for either, so restarts never duplicate it."""
        # "--" ends pgrep's own options: the patterns start with "--chapters".
        result = subprocess.run(["pgrep", "-f", "--", pattern], capture_output=True, text=True)
        if result.returncode != 0:
            return False
        own = {str(proc.pid) for proc in self.procs.values() if proc.poll() is None}
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

    def spawn(self, name: str, command: list[str], extra_env: dict | None = None) -> None:
        if self.dry:
            self.log(f"[dry] would start {name}: {' '.join(command[:8])} ...")
            return
        env = {**os.environ, **BASE_ENV, **(extra_env or {})}
        env.update(reference_image_env(env))
        log_path = self.tmp / f"{name}.log"
        with log_path.open("ab") as handle:
            handle.write(f"\n===== {datetime.now():%F %T} {' '.join(command)}\n".encode())
            self.procs[name] = subprocess.Popen(command, cwd=REPO, env=env, stdout=handle, stderr=subprocess.STDOUT,
                                                stdin=subprocess.DEVNULL, start_new_session=True)
        self.log(f"started {name} (pid {self.procs[name].pid})")

    def stop(self, name: str, why: str) -> None:
        proc = self.procs.get(name)
        if proc is None or proc.poll() is not None:
            return
        if self.dry:
            self.log(f"[dry] would stop {name}: {why}")
            return
        os.killpg(proc.pid, signal.SIGTERM)
        self.log(f"stopped {name}: {why}")

    # ------------------------------------------------------------------ rules
    def key_env(self, key: dict) -> dict:
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

    def compatible(self, key: dict, r: dict) -> bool:
        return r["plan_mode"] == int(key["clip_cap"])

    def _initial_limit(self, key: dict) -> int:
        """Resume the AIMD where the last conductor left it: a restart is not a throttle.
        Falls back to the configured start when the pool has no limit file yet."""
        lo, hi = int(key["inflight"]["min"]), int(key["inflight"]["max"])
        return max(lo, min(hi, self.read_limit(key, int(key["inflight"]["start"]))))

    def pool_dir(self, key: dict) -> Path:
        if key.get("inflight_dir"):
            return Path(key["inflight_dir"])
        return self.novel_dir / (f".inflight-{key['pool']}" if key.get("pool") else ".inflight")

    def owns_limit(self, key: dict) -> bool:
        """One conductor drives the AIMD on a shared pool; the others follow what they read.
        Ownership is a pid beside the limit and passes on when that process is gone."""
        if not key.get("inflight_dir"):
            return True
        path = self.pool_dir(key) / "limit.owner"
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
        if not self.dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(os.getpid()), encoding="utf-8")
            self.log(f"{key['name']}: taking the in-flight limit for {self.pool_dir(key)}")
        return True

    def read_limit(self, key: dict, fallback: int) -> int:
        try:
            return int((self.pool_dir(key) / "limit").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return fallback

    def write_limit(self, key: dict, limit: int) -> None:
        directory = self.pool_dir(key)
        directory.mkdir(parents=True, exist_ok=True)
        if not self.dry:
            (directory / "limit").write_text(str(limit), encoding="utf-8")

    def tick_aimd(self, name: str, key: dict, chapters: list[int]) -> None:
        lane = self.lanes[name]
        aimd = self.cfg.get("aimd", {})
        now = time.time()
        # Count only the lines since the last limit change: one burst of 429s halves the
        # limit once, instead of on every tick until the burst ages out of the window.
        window = int(min(aimd.get("window_seconds", 600), max(1.0, now - lane["limit_changed"])))
        quota = self.recent_lines(chapters, "quota_not_enough", aimd.get("window_seconds", 600)) if chapters else 0
        if quota:
            park = max(1800, int(aimd.get("park_seconds", 3600)))
            self.log(f"{name}: QUOTA EXHAUSTED - {quota} x HTTP 403 quota_not_enough; parking this key for {park} s "
                     "and stopping its lane (top the account up, then restart the conductor)")
            lane["parked_until"] = now + park
            return
        n429 = self.recent_lines(chapters, "HTTP 429", window) if chapters else 0
        lo, hi = int(key["inflight"]["min"]), int(key["inflight"]["max"])
        if n429 >= aimd.get("decrease_at", 5):
            new = max(lo, lane["limit"] // 2)
            if new != lane["limit"]:
                self.log(f"{name}: {n429} x 429 in the window; in-flight {lane['limit']} -> {new}")
                lane["limit"], lane["limit_changed"] = new, now
            lane["throttled_since"] = lane["throttled_since"] or now
            if lane["limit"] == lo and now - lane["throttled_since"] > aimd.get("park_after_seconds", 1800):
                lane["parked_until"] = now + aimd.get("park_seconds", 3600)
                lane["throttled_since"] = None
                self.log(f"{name}: throttled at the floor for too long; parked for {aimd.get('park_seconds', 3600)} s")
        else:
            if n429 == 0:
                lane["throttled_since"] = None
            if n429 == 0 and lane["limit"] < hi and now - lane["limit_changed"] >= aimd.get("increase_after_seconds", 600):
                lane["limit"] = min(hi, lane["limit"] + aimd.get("increase_step", 2))
                lane["limit_changed"] = now
                self.log(f"{name}: calm; in-flight -> {lane['limit']}")
        if self.owns_limit(key):
            self.write_limit(key, lane["limit"])
        else:  # another conductor drives this pool; follow it so this lane's view stays true
            lane["limit"] = max(int(key["inflight"]["min"]), min(int(key["inflight"]["max"]), self.read_limit(key, lane["limit"])))

    def range_finished(self, r: dict, stats: dict) -> bool:
        blocks_done = all(b["done"] for b in self.blocks if b["a"] >= r["a"] and b["b"] <= r["b"])
        batch = self.external_running(f"--chapters {r['a']}-{r['b']} --stage render", r["plan_mode"])
        return blocks_done and not stats["renderable"] and not batch

    def tick_lanes(self, stats: dict[int, dict]) -> None:
        now = time.time()
        assigned = {tuple(l["range"]) for l in self.lanes.values() if l["range"]}
        for name, key in self.keys.items():
            lane = self.lanes[name]
            if now < lane["parked_until"]:
                self.stop(f"lane_{name}", "key parked")
                lane["range"] = None
                continue
            current = next((r for r in self.ranges if lane["range"] and [r["a"], r["b"], r["plan_mode"]] == list(lane["range"])), None)
            if current and self.range_finished(current, stats[id(current)]):
                self.log(f"{name}: range {current['a']}-{current['b']} finished")
                lane["range"] = None
                current = None
            if current is None:
                free = [r for r in self.ranges if self.compatible(key, r) and (r["a"], r["b"], r["plan_mode"]) not in assigned and not self.range_finished(r, stats[id(r)])]
                busy = [r for r in self.ranges if self.compatible(key, r) and (r["a"], r["b"], r["plan_mode"]) in assigned
                        and len(stats[id(r)]["renderable"]) >= 2 * int(key["parallel"])]
                pick = free[0] if free else (max(busy, key=lambda r: len(stats[id(r)]["renderable"])) if busy else None)
                if pick is None:
                    continue
                lane["range"] = [pick["a"], pick["b"], pick["plan_mode"]]
                assigned.add((pick["a"], pick["b"], pick["plan_mode"]))
                current = pick
                self.log(f"{name}: takes range {pick['a']}-{pick['b']} (plan mode {pick['plan_mode']} s)")
                self.ensure_prepass(pick)
            if not self.alive(f"lane_{name}") and now >= lane["next_round_at"]:
                r = current
                if self.external_running(f"--chapters {r['a']}-{r['b']} --stage render", r["plan_mode"]):
                    continue  # a lane started outside the conductor is still on this range: adopt, do not duplicate
                command = [PY, str(SCRIPTS / "thin_batch.py"), "--novel-dir", str(self.novel_dir), "--chapters", f"{r['a']}-{r['b']}",
                           "--stage", "render", "--tier", "fast", "--merge", "1", "--parallel", str(key["parallel"]), "--workers", "0",
                           "--inflight", str(key["inflight"]["max"]), "--plan-mode", str(r["plan_mode"]),
                           # render.prescreen: the local Qwen scores each prompt for content-filter risk and softens the
                           # wording before the first submission (worth it now that planning no longer queues on Qwen).
                           *([] if self.cfg.get("render", {}).get("prescreen") else ["--no-prescreen"]), "--prune"]
                self.spawn(f"lane_{name}", command, self.key_env(key))
                lane["next_round_at"] = now + self.cfg.get("round_gap_seconds", 120)
            elif not self.alive(f"lane_{name}"):
                pass  # between rounds

    def ensure_prepass(self, r: dict) -> None:
        name = f"prepass_{r['a']}_{r['b']}"
        if self.alive(name):
            return
        todo = self.cards_todo(r)
        if not todo:
            return
        self.log(f"card pre-pass for {r['a']}-{r['b']}: {len(todo)} cards")
        groups = [todo[i::4] for i in range(4)]
        for i, group in enumerate(groups):
            if group:
                self.spawn(f"{name}_w{i}", [PY, str(SCRIPTS / "build_cards_thin.py"), "--novel-dir", str(self.novel_dir),
                                            "--assets", ",".join(group), "--review", "--tier", "fast"])

    def cards_todo(self, r: dict) -> list[str]:
        assets = self.novel_dir / "series_assets"
        try:
            report = json.loads((assets / "cards_review.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {"characters": {}, "locations": {}}
        wanted = set()
        for n in range(r["a"], r["b"] + 1):
            plan = self.episode_dir(n) / "clip_plan.json"
            if not plan.is_file():
                continue
            try:
                for clip in json.loads(plan.read_text(encoding="utf-8")).get("clips", []):
                    for ref in clip.get("references", []):
                        if ref.get("role") in ("character", "location") and ref.get("asset_id"):
                            wanted.add(ref["asset_id"])
            except (OSError, ValueError):
                pass
        todo = []
        for asset_id in sorted(wanted):
            kind = "characters" if asset_id.startswith("character_") else "locations"
            images = [p for p in (assets / kind / asset_id).glob("*.jpeg") if not p.name.endswith("-rejected.jpeg")]
            entry = (report.get(kind) or {}).get(asset_id)
            if not images or not entry or not entry.get("judged_at") or max(p.stat().st_mtime for p in images) > float(entry["judged_at"]):
                todo.append(asset_id)
        return todo

    def planning_models(self) -> list[dict]:
        """Servers the planning blocks may use, each with its own slot count.  Empty means
        the conductor's own environment, which is one server."""
        return list(self.cfg.get("planning", {}).get("models") or [])

    def free_server(self) -> dict | None:
        """A server with a slot to spare, fullest-first so blocks bunch on one box rather
        than spreading thin across all of them."""
        models = self.planning_models()
        if not models:
            return None
        used: dict[str, int] = {}
        for block in self.blocks:
            if block.get("server") and block["proc"] and self.alive(block["proc"]):
                used[block["server"]] = used.get(block["server"], 0) + 1
        free = [m for m in models if used.get(m["model"], 0) < int(m.get("slots", 1))]
        return max(free, key=lambda m: used.get(m["model"], 0)) if free else None

    def server_env(self, server: dict) -> dict:
        env = {"QWEN38_LOCAL_BASE_URL": server["base"], "QWEN38_LOCAL_MODEL": server["model"],
               "NOVEL_LLM_BASE_URL": server["base"].split(",")[0], "NOVEL_LLM_MODEL": server["model"]}
        env["QWEN38_LOCAL_API_KEY_VAR"] = server.get("key_var", "")  # named, never the key itself
        return env

    def tick_planning(self, congested: bool) -> None:
        plan_cfg = self.cfg["planning"]
        now = time.time()
        for block in self.blocks:
            if block["done"]:
                continue
            chapters = range(block["a"], block["b"] + 1)
            if block["proc"] and not self.alive(block["proc"]):
                block["proc"] = None
                block["server"] = None
                block["runs"] += 1
                unplanned = [n for n in chapters if not self.settled(n)]
                if unplanned and block["runs"] < PLAN_BLOCK_RUNS:
                    # A block's process ending is not its chapters being planned: a crash, a kill or a chapter
                    # that failed its checks leaves some without a plan, and taking the end for done left 29 of
                    # 诸天's chapters unplanned for good (2026-09-11).  The next run plans the failed ones again.
                    block["retry_at"] = now + PLAN_RETRY_SECONDS
                    self.log(f"planning block {block['a']}-{block['b']} ended with {len(unplanned)} chapter(s) unplanned "
                             f"{unplanned[:8]}; again in {PLAN_RETRY_SECONDS // 60} min (run {block['runs']}/{PLAN_BLOCK_RUNS})")
                    continue
                block["done"] = True
                self.log(f"planning block {block['a']}-{block['b']} finished"
                         + (f"; {len(unplanned)} chapter(s) still unplanned after {block['runs']} runs: {unplanned[:8]}" if unplanned else ""))
                for r in self.ranges:
                    if r["a"] <= block["a"] and block["b"] <= r["b"] and all(b["done"] for b in self.blocks if r["a"] <= b["a"] <= r["b"]):
                        self.ensure_prepass(r)
            elif not block["proc"] and all(self.settled(n) for n in chapters):
                block["done"] = True
        active = [b for b in self.blocks if b["proc"] and self.alive(b["proc"])]
        target = plan_cfg["blocks_min"] if congested else plan_cfg["blocks_max"]
        if len(active) > target:
            for block in sorted(active, key=lambda b: -b["started"])[: len(active) - target]:
                self.stop(block["proc"], "Qwen congested; planning cut back")
                block["proc"] = None
            return
        read_upto = self.read_upto()
        pending = [b for b in self.blocks if not b["done"] and not b["proc"] and b["retry_at"] <= now]
        need_read = [b for b in pending if read_upto < b["a"] + plan_cfg["margin"] and read_upto < b["b"]]
        if need_read and not self.alive("story_pass"):
            a, b = read_upto + 1, max(x["b"] for x in self.blocks)
            self.spawn("story_pass", [PY, str(SCRIPTS / "story_pass_thin.py"), "--novel-dir", str(self.novel_dir), "--chapters", f"{a}-{b}"])
        for block in pending:
            if len(active) >= target:
                break
            if read_upto < min(block["b"], block["a"] + plan_cfg["margin"]):
                continue
            name = f"plan_{block['a']}_{block['b']}"
            if self.external_running(f"--chapters {block['a']}-{block['b']} --stage plan"):
                active.append(block)  # planned outside the conductor (e.g. left from a restart): counts toward the target
                continue
            server = self.free_server()
            if self.planning_models() and server is None:
                continue  # every planning server is full; try again next tick
            command = [PY, str(SCRIPTS / "thin_batch.py"), "--novel-dir", str(self.novel_dir), "--chapters", f"{block['a']}-{block['b']}",
                       "--stage", "plan", "--tier", "fast", "--merge", "1", "--max-redo", "2", "--volume-size", "50", "--no-grow-bible"]
            # A server's slots are filled by blocks, one chapter at a time each: thin_batch now honours
            # --plan-parallel, and passing the slot count as well would have put slots x slots requests on it.
            extra = {"NOVEL_CLIP_SECONDS_MAX": "15"} if block["mode"] == 15 else {}
            if server:
                extra = {**extra, **self.server_env(server)}
                self.log(f"planning block {block['a']}-{block['b']} ({block['mode']} s) on {server['model']}")
            self.spawn(name, command, extra)
            block["proc"], block["started"], block["server"] = name, time.time(), (server or {}).get("model")
            active.append(block)

    def tick_review(self, waiting: int, stats: dict[int, dict]) -> None:
        if self.alive("review") or waiting > self.cfg["qwen"].get("waiting_low", 5) or self.external_running("--stage render --review-only"):
            return
        todo = [n for r in self.ranges for n in stats[id(r)]["unreviewed"]][:60]
        if not todo or time.time() - self.last_review < 60:
            return
        self.last_review = time.time()
        for n in todo:  # a final whose review never gets written is let go after a few batches
            final = self._mtime(self.episode_dir(n) / f"{self.novel_id}_{n}.mp4")
            seen, batches = self.review_tries.get(n, (final, 0))
            self.review_tries[n] = (final, batches + 1 if seen == final else 1)
        self.spawn("review", [PY, str(SCRIPTS / "thin_batch.py"), "--novel-dir", str(self.novel_dir), "--chapters", ",".join(map(str, todo)),
                              "--stage", "render", "--review-only", "--no-render", "--tier", "fast", "--merge", "1", "--parallel", str(self.cfg.get("review", {}).get("parallel", 3))])

    # ------------------------------------------------------------------- loop
    def tick(self) -> bool:
        stats = {id(r): self.range_stats(r) for r in self.ranges}
        waiting = self.qwen_waiting()
        locked = [int(p.parent.name.split("_")[-1]) for p in self.novel_dir.glob(f"{self.novel_id}_*/.render.lock")]
        card_waits = self.recent_lines(locked, "waiting for in-flight redraw", 600)
        congested = waiting > self.cfg["qwen"].get("waiting_high", 40) or card_waits > self.cfg["qwen"].get("card_waits_high", 3)
        if not self.plan_only:
            for name, key in self.keys.items():
                lane = self.lanes[name]
                chapters = list(range(lane["range"][0], lane["range"][1] + 1)) if lane["range"] else []
                self.tick_aimd(name, key, chapters)
            self.tick_lanes(stats)
        self.tick_planning(congested)
        self.tick_review(waiting, stats)
        summary = " | ".join(f"{r['a']}-{r['b']}: done {s['done']}/{s['total']} planned {s['planned']} renderable {len(s['renderable'])} waiting {len(s['blocked'])}"
                             for r, s in ((r, stats[id(r)]) for r in self.ranges))
        pools = " ".join(f"{name}={self.held_slots(k)}/{self.lanes[name]['limit']}" for name, k in self.keys.items())
        self.log(f"tick: {summary} | inflight {pools} | qwen waiting {waiting} card waits {card_waits}{' CONGESTED' if congested else ''}")
        state = {"lanes": self.lanes, "blocks": [{k: v for k, v in b.items()} for b in self.blocks], "time": time.time()}
        (self.tmp / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        if self.plan_only:
            all_done = all(b["done"] for b in self.blocks) and not any(self.alive(n) for n in self.procs if n.startswith("prepass_"))
        else:
            all_done = all(self.range_finished(r, stats[id(r)]) for r in self.ranges)
        # Reviews are part of the work: a conductor that stopped with finals unreviewed - or a review batch still
        # running - left them for nobody, the re-review of judge errors included.
        if self.alive("review") or any(stats[id(r)]["unreviewed"] for r in self.ranges):
            all_done = False
        return not all_done

    def run(self, once: bool) -> None:
        self.log(f"conductor up ({'dry run' if self.dry else 'live'}{', plan only' if self.plan_only else ''}), ranges {[(r['a'], r['b'], r['plan_mode']) for r in self.ranges]}")
        while True:
            more = self.tick()
            if once or not more:
                self.log("conductor done" if not more else "single tick done")
                for name in list(self.procs):
                    if name.startswith("lane_"):
                        self.stop(name, "all ranges finished")
                return
            time.sleep(self.cfg.get("tick_seconds", 90))


def config_for_novel(pipeline: dict, novel_id: str) -> dict:
    """Build one novel's conductor config out of the shared pipeline description."""
    novels = {n["id"]: n for n in pipeline.get("novels", [])}
    if novel_id not in novels:
        raise SystemExit(f"{novel_id} is not in the pipeline file: {sorted(novels)}")
    novel = novels[novel_id]
    resources = pipeline.get("resources", {})
    video = resources.get("video_keys", {})
    models = resources.get("planning_models", {})
    missing = [k for k in novel.get("render_keys", []) if k not in video] + \
              [m for m in novel.get("planning", {}) if m not in models]
    if missing:
        raise SystemExit(f"{novel_id} asks for resources that are not defined: {missing}")
    defaults = pipeline.get("defaults", {})
    planning = {**defaults.get("planning", {}),
                "blocks_max": novel.get("blocks_max", 0), "blocks_min": novel.get("blocks_min", 0),
                "models": [{**models[name], "slots": slots} for name, slots in novel.get("planning", {}).items()]}
    return {
        "novel_dir": f"outputs/{novel_id}",
        "tmp_dir": novel.get("tmp_dir", f"/mnt/disk1/zengzhitao/tmp/conductor-{novel_id}"),
        "tick_seconds": pipeline.get("tick_seconds", 90),
        "round_gap_seconds": pipeline.get("round_gap_seconds", 120),
        "keys": [{"name": name, **video[name]} for name in novel.get("render_keys", [])],
        "ranges": novel["ranges"],
        "planning": planning,
        "qwen": defaults.get("qwen", {}),
        "aimd": defaults.get("aimd", {}),
        "review": defaults.get("review", {}),
        "render": defaults.get("render", {}),
    }


def main() -> int:
    load_dotenv(REPO / ".env")  # before any worker inherits os.environ
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="one novel's conductor config (the older form)")
    parser.add_argument("--pipeline", help="the shared pipeline file; use with --novel")
    parser.add_argument("--novel", help="which novel of the pipeline file to run")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="reading, planning and cards only; no rendering lanes")
    args = parser.parse_args()
    if args.pipeline:
        if not args.novel:
            raise SystemExit("--pipeline needs --novel")
        pipeline = json.loads(Path(args.pipeline).read_text(encoding="utf-8"))
        config = config_for_novel(pipeline, args.novel)
        entry = next(n for n in pipeline["novels"] if n["id"] == args.novel)
        plan_only = args.plan_only or bool(entry.get("plan_only"))
    elif args.config:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
        plan_only = args.plan_only
    else:
        raise SystemExit("give either --config, or --pipeline with --novel")
    Conductor(config, args.dry_run, plan_only).run(args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
