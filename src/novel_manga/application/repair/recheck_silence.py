#!/usr/bin/env python3
"""Recheck existing thin finals' silence gates, without rendering or changing any video.

    PYTHONPATH=src:scripts .venv/bin/python scripts/recheck_silence_thin.py --novel-dir outputs/wuyue --apply

Only current previews that failed silence checks are considered. Other QC and speech verdicts are retained.
Original reports are saved under the run's report directory before applying an update.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import time
from pathlib import Path

from novel_manga.qc import inspect_silence
from novel_manga.util import atomic_write_json, media_duration
from novel_manga.application.production.common import pid_alive
from novel_manga.application.production.runs import episode_status


def recorded_outro(directory: Path, assembly: dict) -> float:
    if "silent_outro_seconds" in assembly:
        return float(assembly["silent_outro_seconds"])
    # Legacy reports did not store the card duration. Require the retained card, the final join segment,
    # and the renderer's settings header to agree, rather than reading today's .env.
    work = directory / "work"
    final = directory / f"{directory.name}.mp4"
    card = work / "outro.mp4"
    values = re.findall(r'"outro_seconds"\s*:\s*([0-9.]+)', (directory / "render.log").read_text(encoding="utf-8"))
    if not values or float(values[-1]) <= 0:
        raise ValueError("no recorded silent outro for this final")
    sequence = [Path(shlex.split(line)[1]) for line in (work / "join_list.txt").read_text().splitlines() if line.startswith("file ")]
    if len(sequence) < 2 or any(p.stat().st_mtime > final.stat().st_mtime for p in (card, sequence[-1])):
        raise ValueError("retained join/card does not precede this final")
    seconds = media_duration(card)
    if abs(seconds - float(values[-1])) > 0.1 or abs(media_duration(sequence[-1]) - seconds) > 0.1:
        raise ValueError("retained outro and final join segment disagree")
    # AAC packet padding accumulates during concat; summing intermediate container durations can cut into
    # the story. Exclude just the known card duration from the actual final's end instead.
    return seconds


def recheck_episode(directory: Path, *, apply: bool, archive: Path) -> dict | None:
    report_path = directory / "thin_media_report.json"
    if episode_status(directory, True) != "done_with_warnings":
        return None
    data = json.loads(report_path.read_text(encoding="utf-8"))
    checks = data["assembly"]["media_qc"]["checks"]
    if not any(checks.get(k, {}).get("passed") is False for k in ("long_silence", "silence_ratio")):
        return None
    if all(checks.get(k, {}).get("detail", {}).get("scope") == "story" for k in ("long_silence", "silence_ratio")):
        return None  # already measured under the corrected rule
    row = {"episode": directory.name, "before": "done_with_warnings"}
    lock = directory / ".render.lock"
    try:
        if lock.is_file():
            pid = int(lock.read_text().strip() or 0)
            if pid and pid_alive(pid):
                return {**row, "skipped": "render is running"}
            if apply:
                lock.unlink()
        if apply:
            with lock.open("x") as handle:
                handle.write(str(os.getpid()))
    except (FileExistsError, ValueError):
        return {**row, "skipped": "episode is locked"}
    try:
        video = directory / f"{directory.name}.mp4"
        inputs = [video, report_path, directory / "clip_plan.json", directory / "review_feedback.json"]

        def stamps():
            return [(p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None for p in inputs]

        before = stamps()
        raw = report_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        assembly = data["assembly"]
        seconds = media_duration(video)
        outro = recorded_outro(directory, assembly)
        updated = inspect_silence(video, seconds, silent_outro_seconds=outro)
        qc = assembly["media_qc"]
        old_checks = {k: qc["checks"][k] for k in updated}
        qc["checks"].update(updated)
        qc["passed"] = all(c["passed"] for c in qc["checks"].values())
        freeze = float(qc["checks"].get("long_freeze", {}).get("detail", {}).get("max_freeze_seconds", 0))
        # The same thin admission as render_clips_thin.py: hold <= 6 s; every other media check must pass.
        assembly.update(silent_outro_seconds=outro, media_qc_passed=qc["passed"],
                        thin_passed=freeze <= 6.0 and all(v["passed"] for k, v in qc["checks"].items() if k != "long_freeze"))
        after = "done" if assembly["thin_passed"] and not data.get("gate_failed_clips") else "done_with_warnings"
        row.update(after=after, old_silence=old_checks, new_silence=updated, gate_failed_clips=data.get("gate_failed_clips", []))
        if stamps() != before:
            return {**row, "skipped": "episode changed during recheck"}
        if apply:
            backup = archive / directory.name
            backup.mkdir(parents=True, exist_ok=True)
            (backup / report_path.name).write_text(raw, encoding="utf-8")
            qc_path = directory / "media_qc_report.json"
            if qc_path.is_file():
                (backup / qc_path.name).write_bytes(qc_path.read_bytes())
            data["silence_recheck"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "backup": str(backup),
                                       "silent_outro_seconds": outro, "video_unchanged": True}
            atomic_write_json(qc_path, qc)
            atomic_write_json(report_path, data)
        return row
    except (OSError, ValueError, KeyError) as error:
        return {**row, "skipped": str(error)}
    finally:
        if apply:
            lock.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    novel = args.novel_dir.resolve()
    archive = novel.parent / "reports" / time.strftime(f"{novel.name}-silence-recheck-%Y%m%d-%H%M%S")
    rows = []
    episodes = sorted((p for p in novel.glob(f"{novel.name}_*") if p.is_dir() and p.name.rsplit("_", 1)[-1].isdigit()),
                      key=lambda p: int(p.name.rsplit("_", 1)[1]))
    for episode in episodes:
        row = recheck_episode(episode, apply=args.apply, archive=archive)
        if row:
            rows.append(row)
            print(json.dumps({k: v for k, v in row.items() if k not in {"old_silence", "new_silence"}}, ensure_ascii=False), flush=True)
    summary = {"checked": sum("skipped" not in r for r in rows), "qualified": sum(r.get("after") == "done" and "skipped" not in r for r in rows),
               "still_warned": sum(r.get("after") == "done_with_warnings" and "skipped" not in r for r in rows),
               "skipped": [r["episode"] for r in rows if "skipped" in r], "applied": args.apply, "report_dir": str(archive)}
    if args.apply:
        atomic_write_json(archive / "summary.json", {"summary": summary, "episodes": rows})
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 1 if summary["skipped"] else 0
