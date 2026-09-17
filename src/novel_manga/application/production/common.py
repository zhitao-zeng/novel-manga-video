"""production_common_thin responsibilities; existing batch execution and retry policy."""
from __future__ import annotations
from novel_manga.application.configuration import project_root
from pathlib import Path
import hashlib
import json
import os
import shutil
import time


ROOT = project_root()


SCRIPTS = ROOT / "scripts"


GROW_STRIDE = 1


PLAN_STAGES = ("plan", "assets", "render")


MODERATION_MARKERS = (".moderation_replanned", ".moderation_replanned2")  # one generic re-plan, then one naming the refused lines


MODERATION_NOTE = ("本章内容有平台审核风险。打斗、威胁、血腥、色情暧昧一律改为间接表现：不写具体暴力动作和伤势，不写露骨或挑逗台词，"
                   "冲突用对峙、退让、旁观者反应和事后结果来交代；避免刀、枪、毒品、赌博、自残等词；台词选原文里克制的句子。")


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def held_submissions(directory: Path) -> int:
    """Clip submissions of the episode held as unconfirmed: a task record with submit_uncertain_at and no task id."""
    count = 0
    for record in directory.glob("work/clips/clip_*/attempt_*/clip.mp4.task.json"):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        count += bool(data.get("submit_uncertain_at") and not data.get("task_id"))
    return count


def parse_chapters(spec: str) -> list[int]:
    chapters: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            chapters.extend(range(int(start), int(end) + 1))
        else:
            chapters.append(int(part))
    return sorted(dict.fromkeys(chapters))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def prune_episode(directory: Path) -> None:
    """Drop what a finished episode no longer needs (~80% of its footprint); the
    clip videos, ASR results and request sidecars stay so re-runs hit the cache."""
    removed = 0
    for pattern in ("clips/*/attempt_*/native*.wav", "clips/*/attempt_*/clip.stale.mp4*", "clips/*/attempt_*/stale_*", "clips/*/attempt_*/native.stale.wav"):
        for path in (directory / "work").glob(pattern):
            path.unlink(missing_ok=True)
            removed += 1
    for sub_dir in ("segments", "review"):
        target = directory / "work" / sub_dir
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            removed += 1
    log(f"{directory.name}: pruned {removed} intermediate files/dirs")
