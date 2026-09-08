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

REPO = Path(__file__).resolve().parent.parent
PY = str(REPO / ".venv" / "bin" / "python")
SCRIPTS = REPO / "scripts"
BASE_ENV = {"PYTHONPATH": "src:scripts", "NOVEL_PLANNER_BACKEND": "deterministic",
            "NOVEL_CREATIVE_PROFILE": "short-drama-adaptive-v1", "PHANROUTER_INLINE_REFERENCE_IMAGES": "1"}


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
        self.ranges = [self._parse_range(r) for r in config["ranges"]]
        self.lanes = {name: {"range": None, "next_round_at": 0.0, "parked_until": 0.0, "limit": k["inflight"]["start"],
                             "limit_changed": time.time(), "throttled_since": None} for name, k in self.keys.items()}
        self.blocks: list[dict] = []  # planning blocks in queue order
        for r in self.ranges:
            size = config["planning"]["block_size"]
            for a in range(r["a"], r["b"] + 1, size):
                self.blocks.append({"a": a, "b": min(r["b"], a + size - 1), "mode": r["plan_mode"], "done": False, "proc": None, "started": 0.0})
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
        plan = d / "clip_plan.json"
        mp4 = d / f"{self.novel_id}_{n}.mp4"
        state = {"n": n, "done": mp4.is_file(), "planned": plan.is_file(), "runs": 0, "mode": None, "unreviewed": False}
        if plan.is_file():
            try:
                policy = json.loads(plan.read_text(encoding="utf-8")).get("policy", "")
                state["mode"] = 15 if "-15s" in policy else 30
            except (OSError, ValueError):
                state["mode"] = 30
            try:
                runs = json.loads((d / ".render_runs").read_text(encoding="utf-8"))
                if runs.get("plan_mtime") == plan.stat().st_mtime:
                    state["runs"] = int(runs.get("runs", 0))
            except (OSError, ValueError):
                pass
        if state["done"]:
            review = d / "episode_review.json"
            state["unreviewed"] = not review.is_file() or review.stat().st_mtime < mp4.stat().st_mtime
        return state

    def range_stats(self, r: dict) -> dict:
        chapters = [self.chapter(n) for n in range(r["a"], r["b"] + 1)]
        renderable = [c["n"] for c in chapters if c["planned"] and not c["done"] and c["runs"] < 3]
        return {"total": len(chapters), "planned": sum(c["planned"] for c in chapters), "done": sum(c["done"] for c in chapters),
                "renderable": renderable, "unreviewed": [c["n"] for c in chapters if c["unreviewed"]]}

    def held_slots(self, pool: str) -> int:
        directory = self.novel_dir / (f".inflight-{pool}" if pool else ".inflight")
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

    def external_running(self, pattern: str) -> bool:
        # "--" ends pgrep's own options: the patterns start with "--chapters".
        return subprocess.run(["pgrep", "-f", "--", pattern], capture_output=True).returncode == 0

    def spawn(self, name: str, command: list[str], extra_env: dict | None = None) -> None:
        if self.dry:
            self.log(f"[dry] would start {name}: {' '.join(command[:8])} ...")
            return
        env = {**os.environ, **BASE_ENV, **(extra_env or {})}
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
        if int(key["clip_cap"]) <= 15:
            env["NOVEL_CLIP_SECONDS_MAX"] = "15"
        return env

    def compatible(self, key: dict, r: dict) -> bool:
        return r["plan_mode"] <= int(key["clip_cap"])

    def write_limit(self, key: dict, limit: int) -> None:
        directory = self.novel_dir / (f".inflight-{key['pool']}" if key.get("pool") else ".inflight")
        directory.mkdir(parents=True, exist_ok=True)
        if not self.dry:
            (directory / "limit").write_text(str(limit), encoding="utf-8")

    def tick_aimd(self, name: str, key: dict, chapters: list[int]) -> None:
        lane = self.lanes[name]
        aimd = self.cfg.get("aimd", {})
        n429 = self.recent_lines(chapters, "HTTP 429", aimd.get("window_seconds", 600)) if chapters else 0
        now = time.time()
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
        self.write_limit(key, lane["limit"])

    def range_finished(self, r: dict, stats: dict) -> bool:
        blocks_done = all(b["done"] for b in self.blocks if b["a"] >= r["a"] and b["b"] <= r["b"])
        batch = self.external_running(f"--chapters {r['a']}-{r['b']} --stage render")
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
            current = next((r for r in self.ranges if lane["range"] and (r["a"], r["b"]) == tuple(lane["range"])), None)
            if current and self.range_finished(current, stats[id(current)]):
                self.log(f"{name}: range {current['a']}-{current['b']} finished")
                lane["range"] = None
                current = None
            if current is None:
                free = [r for r in self.ranges if self.compatible(key, r) and (r["a"], r["b"]) not in assigned and not self.range_finished(r, stats[id(r)])]
                busy = [r for r in self.ranges if self.compatible(key, r) and (r["a"], r["b"]) in assigned
                        and len(stats[id(r)]["renderable"]) >= 2 * int(key["parallel"])]
                pick = free[0] if free else (max(busy, key=lambda r: len(stats[id(r)]["renderable"])) if busy else None)
                if pick is None:
                    continue
                lane["range"] = [pick["a"], pick["b"]]
                assigned.add((pick["a"], pick["b"]))
                current = pick
                self.log(f"{name}: takes range {pick['a']}-{pick['b']} (plan mode {pick['plan_mode']} s)")
                self.ensure_prepass(pick)
            if not self.alive(f"lane_{name}") and now >= lane["next_round_at"]:
                r = current
                if self.external_running(f"--chapters {r['a']}-{r['b']} --stage render"):
                    continue  # a lane started outside the conductor is still on this range: adopt, do not duplicate
                command = [PY, str(SCRIPTS / "thin_batch.py"), "--novel-dir", str(self.novel_dir), "--chapters", f"{r['a']}-{r['b']}",
                           "--stage", "render", "--tier", "fast", "--merge", "1", "--parallel", str(key["parallel"]), "--workers", "0",
                           "--inflight", str(key["inflight"]["max"]), "--no-prescreen", "--prune"]
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

    def tick_planning(self, congested: bool) -> None:
        plan_cfg = self.cfg["planning"]
        for block in self.blocks:
            if block["done"]:
                continue
            if block["proc"] and not self.alive(block["proc"]):
                block["done"] = True
                block["proc"] = None
                self.log(f"planning block {block['a']}-{block['b']} finished")
                for r in self.ranges:
                    if r["a"] <= block["a"] and block["b"] <= r["b"] and all(b["done"] for b in self.blocks if r["a"] <= b["a"] <= r["b"]):
                        self.ensure_prepass(r)
            elif not block["proc"] and all(self.chapter(n)["planned"] for n in range(block["a"], block["b"] + 1)):
                block["done"] = True
        active = [b for b in self.blocks if b["proc"] and self.alive(b["proc"])]
        target = plan_cfg["blocks_min"] if congested else plan_cfg["blocks_max"]
        if len(active) > target:
            for block in sorted(active, key=lambda b: -b["started"])[: len(active) - target]:
                self.stop(block["proc"], "Qwen congested; planning cut back")
                block["proc"] = None
            return
        read_upto = self.read_upto()
        pending = [b for b in self.blocks if not b["done"] and not b["proc"]]
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
            command = [PY, str(SCRIPTS / "thin_batch.py"), "--novel-dir", str(self.novel_dir), "--chapters", f"{block['a']}-{block['b']}",
                       "--stage", "plan", "--tier", "fast", "--merge", "1", "--max-redo", "2", "--volume-size", "50", "--no-grow-bible"]
            self.spawn(name, command, {"NOVEL_CLIP_SECONDS_MAX": "15"} if block["mode"] == 15 else {})
            block["proc"], block["started"] = name, time.time()
            active.append(block)

    def tick_review(self, waiting: int, stats: dict[int, dict]) -> None:
        if self.alive("review") or waiting > self.cfg["qwen"].get("waiting_low", 5) or self.external_running("--stage render --review-only"):
            return
        todo = [n for r in self.ranges for n in stats[id(r)]["unreviewed"]][:60]
        if not todo or time.time() - self.last_review < 60:
            return
        self.last_review = time.time()
        self.spawn("review", [PY, str(SCRIPTS / "thin_batch.py"), "--novel-dir", str(self.novel_dir), "--chapters", ",".join(map(str, todo)),
                              "--stage", "render", "--review-only", "--tier", "fast", "--merge", "1", "--parallel", str(self.cfg.get("review", {}).get("parallel", 3))])

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
        summary = " | ".join(f"{r['a']}-{r['b']}: done {s['done']}/{s['total']} planned {s['planned']} renderable {len(s['renderable'])}"
                             for r, s in ((r, stats[id(r)]) for r in self.ranges))
        pools = " ".join(f"{name}={self.held_slots(k.get('pool', ''))}/{self.lanes[name]['limit']}" for name, k in self.keys.items())
        self.log(f"tick: {summary} | inflight {pools} | qwen waiting {waiting} card waits {card_waits}{' CONGESTED' if congested else ''}")
        state = {"lanes": self.lanes, "blocks": [{k: v for k, v in b.items()} for b in self.blocks], "time": time.time()}
        (self.tmp / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        if self.plan_only:
            all_done = all(b["done"] for b in self.blocks) and not any(self.alive(n) for n in self.procs if n.startswith("prepass_"))
        else:
            all_done = all(self.range_finished(r, stats[id(r)]) for r in self.ranges)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--plan-only", action="store_true", help="reading, planning and cards only; no rendering lanes")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    Conductor(config, args.dry_run, args.plan_only).run(args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
