"""conductor_flow_thin responsibilities; existing production limits and launch policy."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import json
import os
import subprocess
import time
import conductor_capacity_thin as conductor_capacity
import conductor_common_thin as conductor_common
import conductor_dispatch_thin as conductor_dispatch
import conductor_state_thin as conductor_state
import conductor_workers_thin as conductor_workers

class Conductor:
    def __init__(self, config: dict, dry_run: bool, plan_only: bool = False):
        self.cfg = config
        self.dry = dry_run
        self.plan_only = plan_only  # reading, planning and cards only: no rendering lanes
        self.novel_dir = (conductor_common.REPO / config["novel_dir"]).resolve()
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
        self.lanes = {name: {"range": None, "next_round_at": 0.0, "parked_until": 0.0, "limit": conductor_capacity._initial_limit(self, k),
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


    # -------------------------------------------------------------- processes


    # ------------------------------------------------------------------ rules


    # ------------------------------------------------------------------- loop
    def tick(self) -> bool:
        stats = {id(r): conductor_state.range_stats(self, r) for r in self.ranges}
        waiting = conductor_state.qwen_waiting(self)
        locked = [int(p.parent.name.split("_")[-1]) for p in self.novel_dir.glob(f"{self.novel_id}_*/.render.lock")]
        card_waits = conductor_state.recent_lines(self, locked, "waiting for in-flight redraw", 600)
        congested = waiting > self.cfg["qwen"].get("waiting_high", 40) or card_waits > self.cfg["qwen"].get("card_waits_high", 3)
        if not self.plan_only:
            for name, key in self.keys.items():
                lane = self.lanes[name]
                chapters = list(range(lane["range"][0], lane["range"][1] + 1)) if lane["range"] else []
                conductor_capacity.tick_aimd(self, name, key, chapters)
            conductor_dispatch.tick_lanes(self, stats)
        conductor_dispatch.tick_planning(self, congested)
        conductor_dispatch.tick_review(self, waiting, stats)
        summary = " | ".join(f"{r['a']}-{r['b']}: done {s['done']}/{s['total']} planned {s['planned']} renderable {len(s['renderable'])} waiting {len(s['blocked'])}"
                             for r, s in ((r, stats[id(r)]) for r in self.ranges))
        pools = " ".join(f"{name}={conductor_capacity.held_slots(self, k)}/{self.lanes[name]['limit']}" for name, k in self.keys.items())
        self.log(f"tick: {summary} | inflight {pools} | qwen waiting {waiting} card waits {card_waits}{' CONGESTED' if congested else ''}")
        state = {"lanes": self.lanes, "blocks": [{k: v for k, v in b.items()} for b in self.blocks], "time": time.time(),
                 "pid": os.getpid(), "status": "running",
                 "workers": {name: proc.pid for name, proc in self.procs.items() if proc.poll() is None},
                 "work": {"pending": len({n for row in stats.values() for n in row['pending']}),
                          "blocked": len({n for row in stats.values() for n in row['blocked']})}}
        if self.plan_only:
            all_done = all(b["done"] for b in self.blocks) and not any(conductor_workers.alive(self, n) for n in self.procs if n.startswith("prepass_"))
        else:
            all_done = all(conductor_dispatch.range_finished(self, r, stats[id(r)]) for r in self.ranges)
        # Reviews are part of the work: a conductor that stopped with finals unreviewed - or a review batch still
        # running - left them for nobody, the re-review of judge errors included.
        if conductor_workers.alive(self, "review") or any(stats[id(r)]["unreviewed"] for r in self.ranges):
            all_done = False
        state["status"] = "complete" if all_done else "running"
        (self.tmp / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return not all_done

    def run(self, once: bool) -> None:
        self.log(f"conductor up ({'dry run' if self.dry else 'live'}{', plan only' if self.plan_only else ''}), ranges {[(r['a'], r['b'], r['plan_mode']) for r in self.ranges]}")
        while True:
            more = self.tick()
            if once or not more:
                self.log("conductor done" if not more else "single tick done")
                for name in list(self.procs):
                    if name.startswith("lane_"):
                        conductor_workers.stop(self, name, "all ranges finished")
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
