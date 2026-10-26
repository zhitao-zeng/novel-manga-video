"""conductor_flow_thin responsibilities; existing production limits and launch policy."""
from __future__ import annotations
import novel_manga.episodes as ep_names
from datetime import datetime
from pathlib import Path
import json
import os
import subprocess
import time
from novel_manga.application.configuration import RuntimePaths
import novel_manga.application.production.conductor_capacity as conductor_capacity
import novel_manga.application.production.conductor_common as conductor_common
import novel_manga.application.production.conductor_dispatch as conductor_dispatch
import novel_manga.application.production.conductor_state as conductor_state
import novel_manga.application.production.conductor_workers as conductor_workers

class Conductor:
    def __init__(self, config: dict, dry_run: bool, plan_only: bool = False):
        self.cfg = config
        self.dry = dry_run
        self.plan_only = plan_only  # reading, planning and cards only: no rendering lanes
        self.novel_dir = (conductor_common.REPO / config["novel_dir"]).resolve()
        self.novel_id = self.novel_dir.name
        from novel_manga.util import read_json
        self.workspace_index = config.get('workspace_index')
        self.workspaces = read_json(Path(self.workspace_index), {}) if self.workspace_index else {}
        self.tmp = Path(config.get("tmp_dir", RuntimePaths(conductor_common.REPO).temporary / "conductor"))
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.quota_stop_file = self.tmp / "quota-exhausted" if config.get("stop_on_quota_exhausted") else None
        if self.quota_stop_file and not self.dry:
            self.quota_stop_file.unlink(missing_ok=True)
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
                                    "min_seconds": r["min_seconds"], "runs": 0, "retry_at": 0.0})
        self.prepassed: dict[str, int] = {}
        self.last_review = 0.0
        self.log_path = self.tmp / "conductor.log"
        self.executor = config.get('executor', 'standard')
        if self.executor == 'episode_agent':
            if self.plan_only:
                raise ValueError('episode_agent requires its complete workflow; --plan-only uses the standard executor')
            from . import episode_agents
            episode_agents.configure(self)
        elif self.executor != 'standard':
            raise ValueError('executor must be standard or episode_agent')

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _parse_range(r: dict) -> dict:
        a, b = (int(x) for x in str(r["chapters"]).split("-"))
        return {"a": a, "b": b, "plan_mode": int(r.get("plan_mode", 30)), "prepass_after_plan": False,
                "min_seconds": float(r.get("min_seconds", 0)),
                **({'render_keys': list(r['render_keys'])} if r.get('render_keys') else {})}

    def log(self, message: str) -> None:
        line = f"{datetime.now():%m-%d %H:%M:%S} {message}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def episode_dir(self, n: int) -> Path:
        if self.workspaces:
            return Path(self.workspaces[str(n)]['episode_dir'])
        return self.novel_dir / f"{self.novel_id}_{n}"


    # -------------------------------------------------------------- processes


    # ------------------------------------------------------------------ rules


    # ------------------------------------------------------------------- loop
    def tick(self) -> bool:
        if self.executor == 'episode_agent':
            from . import episode_agents
            return episode_agents.tick(self)
        stats = {id(r): conductor_state.range_stats(self, r) for r in self.ranges}
        waiting = conductor_state.qwen_waiting(self)
        locked = [ep_names.chapter_of(p.parent.name) for p in self.novel_dir.glob(f"{self.novel_id}_*/.render.lock")]
        card_waits = conductor_state.recent_lines(self, locked, "waiting for in-flight redraw", 600)
        congested = waiting > self.cfg["qwen"].get("waiting_high", 40) or card_waits > self.cfg["qwen"].get("card_waits_high", 3)
        quota_stopped = bool(self.quota_stop_file and self.quota_stop_file.is_file())
        if not self.plan_only and not quota_stopped:
            for name, key in self.keys.items():
                lane = self.lanes[name]
                chapters = ([n for r in self.ranges if conductor_capacity.compatible(self, key, r)
                             for n in range(r['a'], r['b']+1)] if self.workspaces else
                            list(range(lane["range"][0], lane["range"][1] + 1)) if lane["range"] else [])
                conductor_capacity.tick_aimd(self, name, key, chapters)
            conductor_dispatch.tick_lanes(self, stats)
        if not quota_stopped:
            conductor_dispatch.tick_planning(self, congested)
        conductor_dispatch.tick_review(self, waiting, stats)
        summary = " | ".join(f"{r['a']}-{r['b']}: done {s['done']}/{s['total']} planned {s['planned']} renderable {len(s['renderable'])} waiting {len(s['blocked'])}"
                             for r, s in ((r, stats[id(r)]) for r in self.ranges))
        pools = " ".join(f"{name}={conductor_capacity.held_slots(self, k)}/{self.lanes[name]['limit']}" for name, k in self.keys.items())
        self.log(f"tick: {summary} | inflight {pools} | qwen waiting {waiting} card waits {card_waits}{' CONGESTED' if congested else ''}")
        state = {"executor": 'standard', "lanes": self.lanes, "blocks": [{k: v for k, v in b.items()} for b in self.blocks], "time": time.time(),
                 "pid": os.getpid(), "status": "running",
                 "workers": {name: proc.pid for name, proc in self.procs.items() if proc.poll() is None},
                 "work": {"pending": len({n for row in stats.values() for n in row['pending']}),
                          "blocked": len({n for row in stats.values() for n in row['blocked']})}}
        if self.workspaces:
            state['batch_pipeline'] = {
                'planned': sum(s['planned'] for s in stats.values()),
                'first_complete': sum(s['done'] for s in stats.values()),
                'ready_chapters': sum(len(s['renderable']) for s in stats.values()),
                'ready_clips': sum(s.get('ready_clips',0) for s in stats.values()),
                'in_process_clips': sum(s.get('in_process_clips',0) for s in stats.values()),
                'blocked_clips': sum(s.get('blocked_clips',0) for s in stats.values()),
                'accepted': sum(s.get('accepted',0) for s in stats.values()),
                'review_state': str(self.tmp/'agent-review/state.json')}
        if self.plan_only:
            all_done = all(b["done"] for b in self.blocks) and not any(conductor_workers.alive(self, n) for n in self.procs if n.startswith("prepass_"))
        else:
            all_done = all(conductor_dispatch.range_finished(self, r, stats[id(r)]) for r in self.ranges)
        # Reviews are part of the work: a conductor that stopped with finals unreviewed - or a review batch still
        # running - left them for nobody, the re-review of judge errors included.
        if conductor_workers.alive(self, "review") or any(stats[id(r)]["unreviewed"] for r in self.ranges):
            all_done = False
        state["status"] = "complete" if all_done else "running"
        if self.workspaces:
            from .workspaces import current, busy, chapter as workspace_chapter
            waiting_assets=False;unprepared=False
            owned = sum(busy(row) for row in self.workspaces.values())
            state['batch_pipeline']['owned_chapters'] = owned
            for row in self.workspaces.values():
                if workspace_chapter(row)['done']:
                    continue
                receipt=current(row)
                waiting_assets |= bool(receipt and receipt.get('status')=='waiting_assets')
                unprepared |= receipt is None
            active=bool(state['workers']) or bool(owned) or getattr(self,'review_more',False)
            more=active or waiting_assets or unprepared
            all_done=not more
            state['status']=('waiting_assets' if waiting_assets and not active and not unprepared else 'running') if more else (
                'complete' if state['batch_pipeline']['accepted']==len(self.workspaces) else 'blocked')
        if self.quota_stop_file and self.quota_stop_file.is_file():
            all_done = not state["workers"] and not any(stats[id(r)]["unreviewed"] for r in self.ranges)
            state["status"] = "waiting_quota" if all_done else "draining_quota"
            state["stop_reason"] = "quota_not_enough"
        (self.tmp / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return not all_done

    def run(self, once: bool) -> None:
        self.log(f"conductor up ({'dry run' if self.dry else 'live'}{', plan only' if self.plan_only else ''}), ranges {[(r['a'], r['b'], r['plan_mode']) for r in self.ranges]}")
        while True:
            more = self.tick()
            if once or not more:
                quota_stopped = bool(self.quota_stop_file and self.quota_stop_file.is_file())
                self.log("conductor stopped: quota exhausted; waiting for next scheduled start" if quota_stopped and not more
                         else "episode agent coordinator paused after hosts drained"
                         if self.executor == "episode_agent" and self.agent_pause.is_file()
                         else "conductor done" if not more else "single tick done")
                for name in list(self.procs):
                    if name.startswith("lane_"):
                        conductor_workers.stop(self, name, "all ranges finished")
                return
            time.sleep(self.cfg.get("tick_seconds", 90))
