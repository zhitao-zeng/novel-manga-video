"""dashboard_inventory_thin responsibilities; existing dashboard metric definitions."""
from __future__ import annotations
from pathlib import Path
from novel_manga.application.dashboard.metrics import pipeline_metrics
from novel_manga.application.production.runs import RENDER_RUNS_PER_PLAN
from novel_manga.application.production.runs import REVIEW_POLICY
from novel_manga.application.production.runs import episode_status
from novel_manga.application.production.runs import gate_failures
from novel_manga.application.production.runs import render_runs
import json
import os
import time
import novel_manga.application.dashboard.config as dashboard_config
from novel_manga.application.dashboard.store import scan_book

def _read_tail(path: Path, lines: int = 400) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            block = min(size, lines * 400)
            handle.seek(size - block)
            return handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


_EPISODE_CACHE: dict[tuple[str, bool], tuple[tuple, dict]] = {}


_MODE_CACHE: dict[str, tuple[float, str]] = {}


_DELIVERY_CACHE: dict[str, tuple[float, dict | None]] = {}


def _episode_state(directory: Path, h3_lane: bool, *, scan=None) -> dict:
    """Read the same completion/gate state as the production lane; cache until an input changes.

    A video left by an earlier plan or a failed quality check is still watchable, but is not a completed episode.
    Review status is separate: a model error is unjudged work, not a verdict about video quality.
    """
    scan = scan if scan is not None else scan_book(directory.parent)
    memo_key = (directory, h3_lane)
    if memo_key in scan.states:
        return scan.states[memo_key]
    names = ("clip_plan.json", "thin_media_report.json", "review_feedback.json", f"{directory.name}.mp4",
             ".render_runs", "episode_review.json")
    stamps = tuple(scan.mtime(directory / name) for name in names)
    key = (str(directory), h3_lane)
    hit = _EPISODE_CACHE.get(key)
    if hit and hit[0] == stamps:
        scan.states[memo_key] = hit[1]
        return hit[1]
    unreadable = False
    try:
        status = episode_status(directory, h3_lane)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        status, unreadable = "pending", True
    try:
        report = scan.read(directory / "thin_media_report.json", {})
    except (OSError, ValueError):
        report = {}
    runs = render_runs(directory)
    uncertain = status != "done" and any("SubmissionUncertain" in str(row.get("error", ""))
                                         for row in report.get("clips", []))
    blocked = bool(stamps[0]) and status != "done" and (
        runs >= RENDER_RUNS_PER_PLAN or uncertain or unreadable
        or (status == "done_with_warnings" and not (h3_lane and gate_failures(directory))))
    review_state = "not_ready"
    if status in {"done", "done_with_warnings"} and stamps[3]:
        review_state = "pending"
        if stamps[5] >= stamps[3]:
            try:
                review = scan.read(directory / "episode_review.json", {})
                reviews = review.get("clips", {}) if review.get("policy") == REVIEW_POLICY else {}
                plan = scan.read(directory / "clip_plan.json", {})
                expected = {c["clip_id"] for c in plan.get("clips", []) if c.get("kind") == "video"}
                if any(c.get("severity") == "review_error" for c in reviews.values()):
                    review_state = "error"
                elif expected and all(reviews.get(cid, {}).get("severity") in {"pass", "minor", "fail"} for cid in expected):
                    review_state = "reviewed"
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
    value = {"status": status, "planned": bool(stamps[0]), "final_mtime": stamps[3], "blocked": blocked,
             "uncertain": uncertain, "unreadable": unreadable, "review": review_state, "runs": runs}
    scan.states[memo_key] = value
    _EPISODE_CACHE[key] = (stamps, value)
    return value


def _episode_inventory(novel_id: str, *, scan=None) -> dict:
    """Counts and qualified-final mtimes, shared by the live page and analytics board."""
    result = {"finals": [], "planned": 0, "files": 0, "states": {}, "blocked": 0, "uncertain": 0,
              "review_pending": 0, "review_errors": 0, "attention": []}
    keys = dashboard_config._lane_keys().get(novel_id, [])
    h3_lane = bool(keys) and all(k.get("base_url") for k in keys)
    base = dashboard_config.ROOT / "outputs" / novel_id
    scan = scan if scan is not None else scan_book(base)
    for directory in sorted(scan.directories, key=lambda p: int(p.name.rsplit('_', 1)[-1])):
        state = _episode_state(directory, h3_lane, scan=scan)
        status = state["status"]
        result["planned"] += state["planned"]
        result["files"] += bool(state["final_mtime"])
        result["states"][status] = result["states"].get(status, 0) + 1
        if status == "done":
            result["finals"].append(state["final_mtime"])
        result["blocked"] += state["blocked"]
        result["uncertain"] += state["uncertain"]
        result["review_pending"] += state["review"] == "pending"
        result["review_errors"] += state["review"] == "error"
        reason = ("提交结果不明，需核账" if state["uncertain"] else "状态文件无法读取" if state["unreadable"]
                  else "重试次数用尽，需处理" if state["blocked"] and state["runs"] >= RENDER_RUNS_PER_PLAN
                  else "质检未通过，需处理" if state["blocked"]
                  else "质检未通过，等待重拍" if status == "done_with_warnings"
                  else "计划或修正已更新，等待重做" if status == "stale"
                  else "片段生成失败" if status == "clips_failed"
                  else "旧视频待重新验证" if state["final_mtime"] and status in {"pending", "no_plan"}
                  else "审查失败，仍未审完" if state["review"] == "error" else "")
        if reason:
            result["attention"].append({"chapter": int(directory.name.rsplit("_", 1)[-1]), "reason": reason})
    return result


def _plan_modes(novel_id: str) -> dict:
    """How many of a novel's plans are cut for 15 s clips and how many for 30 s.

    The planner stamps its policy into every clip plan ("...-15s" for the short mode),
    which is the only place the mode survives; re-reading a plan only when it changes
    keeps this cheap for a two-thousand-episode novel.
    """
    counts = {"15": 0, "30": 0}
    base = dashboard_config.ROOT / "outputs" / novel_id
    try:
        entries = list(os.scandir(base))
    except OSError:
        return counts
    for entry in entries:
        if not entry.is_dir() or not entry.name.startswith(f"{novel_id}_"):
            continue
        path = Path(entry.path) / "clip_plan.json"
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        cached = _MODE_CACHE.get(entry.path)
        if not cached or cached[0] != stamp:
            try:
                policy = json.loads(path.read_text(encoding="utf-8")).get("policy", "")
            except (OSError, ValueError):
                continue
            cached = (stamp, "15" if str(policy).endswith("-15s") else "30")
            _MODE_CACHE[entry.path] = cached
        counts[cached[1]] += 1
    return counts


def _chapters(novel_id: str) -> int:
    try:
        return len(json.loads((dashboard_config.ROOT / "outputs" / novel_id / "novel.json").read_text(encoding="utf-8"))["chapters"])
    except (OSError, ValueError, KeyError):
        return 0


def _rate(finals: list[float], window: float) -> int:
    now = time.time()
    return sum(1 for t in finals if now - t <= window)


def _spark(finals: list[float]) -> list[int]:
    """Finished episodes per hour for the last 24 hours, oldest first."""
    now = time.time()
    start = now - 24 * 3600
    buckets = [0] * 24
    for t in finals:
        if t >= start:
            buckets[min(23, int((t - start) // 3600))] += 1
    return buckets


def _today(finals: list[float]) -> int:
    midnight = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    return sum(1 for t in finals if t >= midnight)


def _delivery(novel_id: str) -> dict | None:
    """The novel-level verdict scripts/delivery_gate_thin.py writes: how many episodes pass the technical and
    review gates, and what stops the rest.  Read from disk, not recomputed here: the script needs the chapter
    text for its shadow gate, and a review batch is what changes the answer (thin_batch runs it after one)."""
    path = dashboard_config.ROOT / "outputs" / novel_id / "delivery.json"
    stamp = _mtime(path)
    if not stamp:
        return None
    hit = _DELIVERY_CACHE.get(str(path))
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("review_policy") != REVIEW_POLICY:
            return None  # recalculate aggregates made before the current review gate
        gates = data.get("gates", {})
        value = {
            "deliverable": data.get("deliverable", 0), "total": data.get("total", 0),
            "generated_at": data.get("generated_at", ""),
            "tech_blocked": gates.get("tech", {}).get("blocked", 0),
            "review_blocked": gates.get("review", {}).get("blocked", 0),
            "must_fix_clips": gates.get("review", {}).get("must_fix_clips", 0),
            "script_flagged": gates.get("script", {}).get("flagged", 0),
            "script_would_block": gates.get("script", {}).get("would_block", 0),
        }
    except (OSError, ValueError, AttributeError, TypeError):
        value = None
    _DELIVERY_CACHE[str(path)] = (stamp, value)
    return value


def _novel_status(novel: dict) -> dict:
    scan = scan_book(dashboard_config.ROOT / "outputs" / novel["id"])
    inventory = _episode_inventory(novel["id"], scan=scan)
    finals, planned = inventory["finals"], inventory["planned"]
    chapters = _chapters(novel["id"])
    per_hour = _rate(finals, 3600)
    recent = _rate(finals, 900) * 4  # last quarter hour, extrapolated
    left = max(0, planned - len(finals))
    speed = recent or per_hour
    tick = ""
    if novel["conductor"]:
        for line in reversed(_read_tail(novel["conductor"], 200)):
            found = dashboard_config.TICK.search(line)
            if found:
                tick = found.group(1)
                break
    return {
        "id": novel["id"], "title": novel["title"], "chapters": chapters, "planned": planned,
        "done": len(finals), "per_hour": per_hour, "recent_per_hour": recent, "left": left,
        "eta_hours": round(left / speed, 1) if speed and not inventory["blocked"] else None,
        "last_final": max(finals) if finals else None, "tick": tick,
        "modes": _plan_modes(novel["id"]),
        "spark": _spark(finals), "today": _today(finals),
        "delivery": _delivery(novel["id"]),
        "pipeline": pipeline_metrics(dashboard_config.ROOT / 'outputs' / novel['id']),
        **{k: v for k, v in inventory.items() if k not in {"finals", "planned"}},
    }
