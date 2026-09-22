"""Select technical re-renders without taking episodes owned by either repair lane.

wy_filler_tried.txt records attempts, not completion. Re-read actual episode status;
retry a failed attempt up to the existing render limit and leave held work visible.
"""
from __future__ import annotations
import novel_manga.episodes as ep_names

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from novel_manga.application.production.runs import RENDER_RUNS_PER_PLAN, episode_status, render_runs


def numbers(path: Path) -> list[int]:
    if not path.is_file():
        return []
    return [int(s) for s in path.read_text().replace("\n", ",").split(",") if s.strip()]


def render_locked(directory: Path) -> bool:
    try:
        pid = int((directory / ".render.lock").read_text().strip())
    except (OSError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def running_scripts(fix: Path) -> set[str]:
    names = {str(fix / name) for name in ("wy_repair_chain.sh", "wy_repair_chain2.sh", "wy_repair_chain2b.sh", "wy_single_card_recovery.py")}
    active = set()
    for path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            active.update(names.intersection(path.read_bytes().decode(errors="replace").split("\0")))
        except OSError:
            continue  # a process finished while the queue was being read
    return active


def select(novel: Path, fix: Path, limit: int = 12) -> dict:
    busy = set()
    active = running_scripts(fix)
    for script, targets in (("wy_repair_chain.sh", "wy_repair_targets.txt"),
                            ("wy_repair_chain2.sh", "wy_repair_targets2.txt"),
                            ("wy_repair_chain2b.sh", "wy_repair_targets2b.txt"),
                            ("wy_single_card_recovery.py", "wy_single_card_recovery_active.txt")):
        if str(fix / script) in active:
            busy.update(numbers(fix / targets))
    tried = Counter(numbers(fix / "wy_filler_tried.txt"))
    result = {"selected": [], "busy": [], "held": [], "unreadable": []}
    dirs = sorted((p for p in novel.glob(f"{novel.name}_*") if p.is_dir() and ep_names.is_episode(p.name)),
                  key=lambda p: ep_names.episode_order(p.name), reverse=True)
    for directory in dirs:
        n = ep_names.chapter_of(directory.name)
        try:
            if episode_status(directory, True) != "stale":
                continue
            if n in busy or render_locked(directory):
                result["busy"].append(n)
            elif tried[n] >= RENDER_RUNS_PER_PLAN or render_runs(directory) >= RENDER_RUNS_PER_PLAN:
                result["held"].append(n)
            elif len(result["selected"]) < limit:
                result["selected"].append(n)
        except (OSError, ValueError, KeyError):
            result["unreadable"].append(n)
    return result


def record(novel: Path, fix: Path, episodes: list[int]) -> dict:
    result = {"finished": [], "retry": [], "locked": []}
    with (fix / "wy_filler_tried.txt").open("a") as stream:
        for n in episodes:
            directory = novel / f"{novel.name}_{n}"
            if render_locked(directory):
                result["locked"].append(n)
                continue  # another renderer owns it; no attempt was spent here
            stream.write(f"{n}\n")
            try:
                status = episode_status(directory, True)
            except (OSError, ValueError, KeyError):
                status = "unreadable"
            result["finished" if status in {"done", "done_with_warnings"} else "retry"].append(n)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["select", "record"])
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--fix-dir", type=Path, required=True)
    parser.add_argument("--episodes", default="")
    args = parser.parse_args()
    if args.action == "select":
        result = select(args.novel_dir, args.fix_dir)
        (args.fix_dir / "wy_filler_queue.json").write_text(json.dumps(result, indent=2))
        print(",".join(map(str, result["selected"])))
    else:
        episodes = [int(s) for s in args.episodes.split(",") if s.strip()]
        print(json.dumps(record(args.novel_dir, args.fix_dir, episodes)))
