"""Current review files and evidence imports, shared by review, audit and repair history.

This module owns reading selected takes and reconciling episode_review.json.
It never creates a verifier, publishes media, or dispatches a repair.
"""
from __future__ import annotations
from novel_manga.application.configuration import project_root
import json
from pathlib import Path
from novel_manga.util import read_json as read, atomic_write_json
from novel_manga.review import reconciliation, storage as review_storage
from novel_manga.review.audit_queue import export_results

ROOT = project_root()


def load_evidence(paths: list[Path]) -> dict:
    result = {}
    for path in paths:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # the running scanner may still be appending its last line
                reconciliation.merge_evidence(result, row)
    return result


def current_evidence(legacy: Path, state_dir: Path) -> tuple[dict, dict]:
    local = load_evidence([legacy / "wy_verify.jsonl", state_dir / "scan_local.jsonl", state_dir / "verified.jsonl"])
    flash = load_evidence([legacy / "wy_verify_flash.jsonl", state_dir / "scan_flash.jsonl"])
    queue = state_dir / "shared_audit.sqlite3"
    if queue.is_file():
        for row in export_results(queue, 'qwen'):
            reconciliation.merge_evidence(local, row)
        for row in export_results(queue, 'flash'):
            reconciliation.merge_evidence(flash, row)
    return local, flash


def current_takes(directory: Path, plan: dict, review: dict) -> dict:
    media = read(directory / "thin_media_report.json", {})
    selected = {c["clip_id"]: c.get("selected") or {} for c in media.get("clips", [])}
    result = {}
    for clip in plan.get("clips", []):
        if clip.get("kind") != "video":
            continue
        cid = clip["clip_id"]
        video = selected.get(cid, {}).get("video") or (review.get("clips", {}).get(cid) or {}).get("video")
        if not video:
            continue
        path = Path(video)
        if not path.is_absolute():
            path = ROOT / path
        take = review_storage.take_identity(path)
        if take:
            result[cid] = {"video": str(path), "take": take}
    return result


def read_reconciled(directory: Path, local: dict, flash: dict) -> tuple[dict, dict, dict]:
    plan = read(directory / "clip_plan.json", {})
    previous = read(directory / "episode_review.json", {})
    takes = current_takes(directory, plan, previous)
    result = reconciliation.reconcile_review(directory, plan, previous, takes, local, flash)
    return previous, result, takes


def write_reconciled(directory: Path, previous: dict, result: dict):
    if result != previous:
        atomic_write_json(directory / "episode_review.json", result)


def reconcile(directory: Path, local: dict, flash: dict, *, write: bool = True) -> tuple[dict, dict]:
    previous, result, takes = read_reconciled(directory, local, flash)
    if write:
        write_reconciled(directory, previous, result)
    return result, takes


def sync_audit_review(directory: Path, state_dir: Path, episode: int, local: dict, flash: dict) -> bool:
    """Back up once, then import current audit evidence without touching media.

    The audit flow has already checked that no repair job owns this episode.
    """
    original=directory/'episode_review.json'
    backup=state_dir/'before_reviews'/f'{episode}.json'
    if original.is_file() and not backup.exists():
        backup.parent.mkdir(parents=True,exist_ok=True)
        backup.write_bytes(original.read_bytes())
    previous=read(original,{})
    result,takes=reconcile(directory,local,flash,write=False)
    updated = reconciliation.merge_audit_review(directory, episode, previous, result, takes, local)
    if updated != previous:
        atomic_write_json(original, updated)
    return True
