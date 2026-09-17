"""dashboard_history_thin responsibilities; existing dashboard metric definitions."""
from __future__ import annotations
from pathlib import Path
from pipeline_dashboard import pipeline_metrics
from review_progress_thin import viewer_progress
from novel_manga.reporting.usage import summarize as usage_summary
from novel_manga.util import read_json
from thin_runs import REVIEW_POLICY
import json
import time
import dashboard_config_thin as dashboard_config
import dashboard_inventory_thin as dashboard_inventory
from dashboard_store_thin import scan_book

def _parse_review(path: Path):
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if report.get("policy") != REVIEW_POLICY:
        return None
    clips = report.get("clips", {})
    sev: dict[str, int] = {}
    cats: dict[str, int] = {}
    for clip in clips.values():
        severity = str(clip.get("severity", "?"))
        sev[severity] = sev.get(severity, 0) + 1
        for key, bad, name in dashboard_config.REVIEW_CATS:
            if clip.get(key) == bad:
                cats[name] = cats.get(name, 0) + 1
    # A clip's severity answers "does this differ from the card at all", which counts a
    # side character's sleeve.  The pipeline only reshoots what fix_tier graded must_fix,
    # and thin_review.py records exactly those in the episode-level feedback map, so its
    # size is the number of clips actually queued to shoot again.  Reading the map rather
    # than the per-clip tier (only stored since 2026-09-12) makes the redo rate available
    # for every review ever written: 雾月 610 clips, against 5529 graded non-pass.
    feedback = report.get("feedback")
    must_fix = len(feedback) if isinstance(feedback, dict) else 0
    return {"sev": sev, "cats": cats, "must_fix": must_fix}


def _parse_media(path: Path):
    try:
        clips = json.loads(path.read_text(encoding="utf-8")).get("clips", [])
    except (OSError, ValueError):
        return None
    return {"clips": len(clips), "attempts": sum(len(c.get("attempts", [])) for c in clips)}


def _parse_render_model(path: Path):
    """The video model an episode was rendered with, from the settings header
    render_clips_thin.py prints into render.log."""
    # A repair run appends a new header. The first header could name Seedance even after the episode moved to H3.
    found = dashboard_config.VIDEO_MODEL.findall("\n".join(dashboard_inventory._read_tail(path, 2000)))
    return found[-1] if found else ""


def _board_novel(novel: dict) -> dict:
    nid = novel["id"]
    scan = scan_book(dashboard_config.ROOT / "outputs" / nid)
    inventory = dashboard_inventory._episode_inventory(nid, scan=scan)
    finals, planned = inventory["finals"], inventory["planned"]
    keys = dashboard_config._lane_keys().get(nid, [])
    h3_lane = bool(keys) and all(k.get("base_url") for k in keys)
    chapters = dashboard_inventory._chapters(nid)
    done = len(finals)
    now = time.time()
    by_day: dict[str, int] = {}
    by_hour: dict[str, int] = {}
    for t in finals:
        day = time.strftime("%Y-%m-%d", time.localtime(t))
        by_day[day] = by_day.get(day, 0) + 1
        hour = time.strftime("%Y-%m-%dT%H:00", time.localtime(t))
        by_hour[hour] = by_hour.get(hour, 0) + 1
    week_ago = now - 7 * 86400
    rate7 = round(sum(1 for t in finals if t >= week_ago) / 7, 1)
    # A book's whole run is a day or two, so the working estimate is the
    # last six hours; the 7-day rate only matters for long-running books.
    rate_h = round(sum(1 for t in finals if t >= now - 6 * 3600) / 6, 1)
    left = max(0, planned - done)
    projected = None
    projected_ts = None
    if left and not inventory["blocked"]:
        if rate7:
            projected = time.strftime("%Y-%m-%d", time.localtime(now + left / rate7 * 86400))
        if rate_h:
            projected_ts = time.strftime("%Y-%m-%dT%H:%M", time.localtime(now + left / rate_h * 3600))
    sev_all: dict[str, int] = {}
    cats: dict[str, int] = {}
    recent_pass = recent_total = 0
    must_all = recent_must = 0
    lanes: dict[str, dict] = {}
    base = dashboard_config.ROOT / "outputs" / nid
    for directory in scan.directories:
        state = dashboard_inventory._episode_state(directory, h3_lane, scan=scan)
        review = scan.parsed(directory / "episode_review.json", _parse_review)
        if state["review"] == "not_ready" or scan.mtime(directory / "episode_review.json") < state["final_mtime"]:
            review = None
        media = scan.parsed(directory / "thin_media_report.json", _parse_media)
        model = scan.parsed(directory / "render.log", _parse_render_model)
        if not (review or media):
            continue
        lane = lanes.setdefault(model or "未知",
                                {"episodes": 0, "clips": 0, "attempts": 0, "sev": {}, "must_fix": 0})
        lane["episodes"] += 1
        if media:
            lane["clips"] += media["clips"]
            lane["attempts"] += media["attempts"]
        if not review:
            continue
        must_all += review.get("must_fix", 0)
        lane["must_fix"] += review.get("must_fix", 0)
        for key, value in review["sev"].items():
            sev_all[key] = sev_all.get(key, 0) + value
            lane["sev"][key] = lane["sev"].get(key, 0) + value
        for key, value in review["cats"].items():
            cats[key] = cats.get(key, 0) + value
        try:
            reviewed_at = scan.mtime(directory / "episode_review.json")
        except OSError:
            reviewed_at = 0
        if reviewed_at >= week_ago:
            recent_total += sum(review["sev"].get(k, 0) for k in ("pass", "minor", "fail"))
            recent_pass += review["sev"].get("pass", 0)
            recent_must += review.get("must_fix", 0)
    total = sum(sev_all.get(k, 0) for k in ("pass", "minor", "fail"))
    lane_rows = []
    for name, lane in sorted(lanes.items()):
        reviewed = sum(lane["sev"].get(k, 0) for k in ("pass", "minor", "fail"))
        lane_rows.append({
            "model": name, "episodes": lane["episodes"], "clips": lane["clips"],
            "avg_attempts": round(lane["attempts"] / lane["clips"], 2) if lane["clips"] else None,
            "pass_rate": round(100 * lane["sev"].get("pass", 0) / reviewed, 1) if reviewed else None,
            "redo_rate": round(100 * lane["must_fix"] / reviewed, 1) if reviewed else None,
            "must_fix": lane["must_fix"],
            "reviewed": reviewed,
            "review_errors": lane["sev"].get("review_error", 0),
        })
    return {
        "id": nid, "title": novel["title"], "done": done, "planned": planned, "chapters": chapters,
        "daily": sorted(by_day.items()), "hourly": sorted(by_hour.items()),
        "rate7": rate7, "projected": projected, "rate_h": rate_h, "projected_ts": projected_ts,
        "delivery": dashboard_inventory._delivery(nid),
        "pipeline": pipeline_metrics(base),
        'per_hour':dashboard_inventory._rate(finals,3600), 'recent_per_hour':dashboard_inventory._rate(finals,900)*4,
        **{k: v for k, v in inventory.items() if k not in {"finals", "planned"}},
        "quality": {
            "clips": total, "sev": sev_all,
            "review_errors": sev_all.get("review_error", 0),
            "pass_rate": round(100 * sev_all.get("pass", 0) / total, 1) if total else None,
            "recent_rate": round(100 * recent_pass / recent_total, 1) if recent_total else None,
            "must_fix": must_all,
            "redo_rate": round(100 * must_all / total, 1) if total else None,
            "recent_redo": round(100 * recent_must / recent_total, 1) if recent_total else None,
            "cats": sorted(cats.items(), key=lambda kv: -kv[1]),
        },
        "viewer_review": viewer_progress(base, h3_lane),
        "usage": usage_summary(base, read_json(dashboard_config.ROOT / "configs/pricing.json", {})),
        "lanes": lane_rows,
    }
