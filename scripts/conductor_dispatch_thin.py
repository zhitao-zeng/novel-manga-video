"""conductor_dispatch_thin responsibilities; existing production limits and launch policy."""
from __future__ import annotations
import json
import time
import conductor_capacity_thin as conductor_capacity
import conductor_common_thin as conductor_common
import conductor_state_thin as conductor_state
import conductor_workers_thin as conductor_workers

def range_finished(conductor, r: dict, stats: dict) -> bool:
    blocks_done = all(b["done"] for b in conductor.blocks if b["a"] >= r["a"] and b["b"] <= r["b"])
    batch = conductor_workers.external_running(conductor, f"--chapters {r['a']}-{r['b']} --stage render", r["plan_mode"])
    return blocks_done and not stats["renderable"] and not batch


def tick_lanes(conductor, stats: dict[int, dict]) -> None:
    now = time.time()
    assigned = {tuple(l["range"]) for l in conductor.lanes.values() if l["range"]}
    for name, key in conductor.keys.items():
        lane = conductor.lanes[name]
        if now < lane["parked_until"]:
            conductor_workers.stop(conductor, f"lane_{name}", "key parked")
            lane["range"] = None
            continue
        current = next((r for r in conductor.ranges if lane["range"] and [r["a"], r["b"], r["plan_mode"]] == list(lane["range"])), None)
        if current and range_finished(conductor, current, stats[id(current)]):
            conductor.log(f"{name}: range {current['a']}-{current['b']} finished")
            lane["range"] = None
            current = None
        if current is None:
            free = [r for r in conductor.ranges if conductor_capacity.compatible(conductor, key, r) and (r["a"], r["b"], r["plan_mode"]) not in assigned and not range_finished(conductor, r, stats[id(r)])]
            busy = [r for r in conductor.ranges if conductor_capacity.compatible(conductor, key, r) and (r["a"], r["b"], r["plan_mode"]) in assigned
                    and len(stats[id(r)]["renderable"]) >= 2 * int(key["parallel"])]
            pick = free[0] if free else (max(busy, key=lambda r: len(stats[id(r)]["renderable"])) if busy else None)
            if pick is None:
                continue
            lane["range"] = [pick["a"], pick["b"], pick["plan_mode"]]
            assigned.add((pick["a"], pick["b"], pick["plan_mode"]))
            current = pick
            conductor.log(f"{name}: takes range {pick['a']}-{pick['b']} (plan mode {pick['plan_mode']} s)")
            ensure_prepass(conductor, pick)
        if not conductor_workers.alive(conductor, f"lane_{name}") and now >= lane["next_round_at"]:
            r = current
            if conductor_workers.external_running(conductor, f"--chapters {r['a']}-{r['b']} --stage render", r["plan_mode"]):
                continue  # a lane started outside the conductor is still on this range: adopt, do not duplicate
            command = [conductor_common.PY, str(conductor_common.SCRIPTS / "thin_batch.py"), "--novel-dir", str(conductor.novel_dir), "--chapters", f"{r['a']}-{r['b']}",
                       "--stage", "render", "--tier", "fast", "--merge", "1", "--parallel", str(key["parallel"]), "--workers", "0",
                       "--inflight", str(key["inflight"]["max"]), "--plan-mode", str(r["plan_mode"]),
                       # render.prescreen: the local Qwen scores each prompt for content-filter risk and softens the
                       # wording before the first submission (worth it now that planning no longer queues on Qwen).
                       *([] if conductor.cfg.get("render", {}).get("prescreen") else ["--no-prescreen"]), "--prune"]
            conductor_workers.spawn(conductor, f"lane_{name}", command, conductor_workers.key_env(conductor, key))
            lane["next_round_at"] = now + conductor.cfg.get("round_gap_seconds", 120)
        elif not conductor_workers.alive(conductor, f"lane_{name}"):
            pass  # between rounds


def ensure_prepass(conductor, r: dict) -> None:
    name = f"prepass_{r['a']}_{r['b']}"
    if conductor_workers.alive(conductor, name):
        return
    todo = cards_todo(conductor, r)
    if not todo:
        return
    conductor.log(f"card pre-pass for {r['a']}-{r['b']}: {len(todo)} cards")
    groups = [todo[i::4] for i in range(4)]
    for i, group in enumerate(groups):
        if group:
            conductor_workers.spawn(conductor, f"{name}_w{i}", [conductor_common.PY, str(conductor_common.SCRIPTS / "build_cards_thin.py"), "--novel-dir", str(conductor.novel_dir),
                                        "--assets", ",".join(group), "--review", "--tier", "fast"])


def cards_todo(conductor, r: dict) -> list[str]:
    assets = conductor.novel_dir / "series_assets"
    try:
        report = json.loads((assets / "cards_review.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        report = {"characters": {}, "locations": {}}
    wanted = set()
    for n in range(r["a"], r["b"] + 1):
        plan = conductor.episode_dir(n) / "clip_plan.json"
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


def planning_models(conductor) -> list[dict]:
    """Servers the planning blocks may use, each with its own slot count.  Empty means
    the conductor's own environment, which is one server."""
    return list(conductor.cfg.get("planning", {}).get("models") or [])


def free_server(conductor) -> dict | None:
    """A server with a slot to spare, fullest-first so blocks bunch on one box rather
    than spreading thin across all of them."""
    models = planning_models(conductor)
    if not models:
        return None
    used: dict[str, int] = {}
    for block in conductor.blocks:
        if block.get("server") and block["proc"] and conductor_workers.alive(conductor, block["proc"]):
            used[block["server"]] = used.get(block["server"], 0) + 1
    free = [m for m in models if used.get(m["model"], 0) < int(m.get("slots", 1))]
    return max(free, key=lambda m: used.get(m["model"], 0)) if free else None


def tick_planning(conductor, congested: bool) -> None:
    plan_cfg = conductor.cfg["planning"]
    now = time.time()
    for block in conductor.blocks:
        if block["done"]:
            continue
        chapters = range(block["a"], block["b"] + 1)
        if block["proc"] and not conductor_workers.alive(conductor, block["proc"]):
            block["proc"] = None
            block["server"] = None
            block["runs"] += 1
            unplanned = [n for n in chapters if not conductor_state.settled(conductor, n)]
            if unplanned and block["runs"] < conductor_common.PLAN_BLOCK_RUNS:
                # A block's process ending is not its chapters being planned: a crash, a kill or a chapter
                # that failed its checks leaves some without a plan, and taking the end for done left 29 of
                # 诸天's chapters unplanned for good (2026-09-11).  The next run plans the failed ones again.
                block["retry_at"] = now + conductor_common.PLAN_RETRY_SECONDS
                conductor.log(f"planning block {block['a']}-{block['b']} ended with {len(unplanned)} chapter(s) unplanned "
                         f"{unplanned[:8]}; again in {conductor_common.PLAN_RETRY_SECONDS // 60} min (run {block['runs']}/{conductor_common.PLAN_BLOCK_RUNS})")
                continue
            block["done"] = True
            conductor.log(f"planning block {block['a']}-{block['b']} finished"
                     + (f"; {len(unplanned)} chapter(s) still unplanned after {block['runs']} runs: {unplanned[:8]}" if unplanned else ""))
            for r in conductor.ranges:
                if r["a"] <= block["a"] and block["b"] <= r["b"] and all(b["done"] for b in conductor.blocks if r["a"] <= b["a"] <= r["b"]):
                    ensure_prepass(conductor, r)
        elif not block["proc"] and all(conductor_state.settled(conductor, n) for n in chapters):
            block["done"] = True
    active = [b for b in conductor.blocks if b["proc"] and conductor_workers.alive(conductor, b["proc"])]
    target = plan_cfg["blocks_min"] if congested else plan_cfg["blocks_max"]
    if len(active) > target:
        for block in sorted(active, key=lambda b: -b["started"])[: len(active) - target]:
            conductor_workers.stop(conductor, block["proc"], "Qwen congested; planning cut back")
            block["proc"] = None
        return
    read_upto = conductor_state.read_upto(conductor)
    pending = [b for b in conductor.blocks if not b["done"] and not b["proc"] and b["retry_at"] <= now]
    need_read = [b for b in pending if read_upto < b["a"] + plan_cfg["margin"] and read_upto < b["b"]]
    if need_read and not conductor_workers.alive(conductor, "story_pass"):
        a, b = read_upto + 1, max(x["b"] for x in conductor.blocks)
        conductor_workers.spawn(conductor, "story_pass", [conductor_common.PY, str(conductor_common.SCRIPTS / "story_pass_thin.py"), "--novel-dir", str(conductor.novel_dir), "--chapters", f"{a}-{b}"])
    for block in pending:
        if len(active) >= target:
            break
        if read_upto < min(block["b"], block["a"] + plan_cfg["margin"]):
            continue
        name = f"plan_{block['a']}_{block['b']}"
        if conductor_workers.external_running(conductor, f"--chapters {block['a']}-{block['b']} --stage plan"):
            active.append(block)  # planned outside the conductor (e.g. left from a restart): counts toward the target
            continue
        server = free_server(conductor)
        if planning_models(conductor) and server is None:
            continue  # every planning server is full; try again next tick
        command = [conductor_common.PY, str(conductor_common.SCRIPTS / "thin_batch.py"), "--novel-dir", str(conductor.novel_dir), "--chapters", f"{block['a']}-{block['b']}",
                   "--stage", "plan", "--tier", "fast", "--merge", "1", "--max-redo", "2", "--volume-size", "50", "--no-grow-bible"]
        # A server's slots are filled by blocks, one chapter at a time each: thin_batch now honours
        # --plan-parallel, and passing the slot count as well would have put slots x slots requests on it.
        extra = {"NOVEL_CLIP_SECONDS_MAX": "15"} if block["mode"] == 15 else {}
        if server:
            extra = {**extra, **conductor_workers.server_env(conductor, server)}
            conductor.log(f"planning block {block['a']}-{block['b']} ({block['mode']} s) on {server['model']}")
        conductor_workers.spawn(conductor, name, command, extra)
        block["proc"], block["started"], block["server"] = name, time.time(), (server or {}).get("model")
        active.append(block)


def tick_review(conductor, waiting: int, stats: dict[int, dict]) -> None:
    if conductor_workers.alive(conductor, "review") or waiting > conductor.cfg["qwen"].get("waiting_low", 5) or conductor_workers.external_running(conductor, "--stage render --review-only"):
        return
    todo = [n for r in conductor.ranges for n in stats[id(r)]["unreviewed"]][:60]
    if not todo or time.time() - conductor.last_review < 60:
        return
    conductor.last_review = time.time()
    for n in todo:  # a final whose review never gets written is let go after a few batches
        final = conductor_state._mtime(conductor.episode_dir(n) / f"{conductor.novel_id}_{n}.mp4")
        seen, batches = conductor.review_tries.get(n, (final, 0))
        conductor.review_tries[n] = (final, batches + 1 if seen == final else 1)
    conductor_workers.spawn(conductor, "review", [conductor_common.PY, str(conductor_common.SCRIPTS / "thin_batch.py"), "--novel-dir", str(conductor.novel_dir), "--chapters", ",".join(map(str, todo)),
                          "--stage", "render", "--review-only", "--no-render", "--tier", "fast", "--merge", "1", "--parallel", str(conductor.cfg.get("review", {}).get("parallel", 3))])
