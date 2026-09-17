"""Stage and publish a reviewed repair candidate, preserving the existing write order.

Repair history records observations and generation attempts separately. This
flow owns the final media replacement and its existing resumable receipt.
"""
from __future__ import annotations
import copy
import time
from pathlib import Path
from novel_manga.util import read_json as read, atomic_write_json
from novel_manga.application.repair.history import HISTORY_DIR, ERROR_FIELDS, copy_complete

CANDIDATE_DIR = "repair_candidate"


def assembly_directory(directory: Path) -> Path:
    if (directory / HISTORY_DIR / "history.json").is_file():
        target = directory / CANDIDATE_DIR
        target.mkdir(parents=True, exist_ok=True)
        return target
    return directory


def publication_pending(directory: Path) -> bool:
    return bool((read(directory / "thin_media_report.json", {}).get("assembly") or {}).get("pending_publish"))


def publish_if_ready(directory: Path, review: dict, takes: dict) -> bool:
    from novel_manga.application.production.runs import REVIEW_POLICY, episode_status
    media = read(directory / "thin_media_report.json", {})
    assembly = media.get("assembly") or {}
    if not assembly.get("pending_publish") or episode_status(directory, True) != "done":
        return False
    if review.get("policy") != REVIEW_POLICY or review.get("feedback"):
        return False
    plan = read(directory / "clip_plan.json", {})
    expected = [c["clip_id"] for c in plan.get("clips", []) if c.get("kind") == "video"]
    if not expected:
        return False
    for cid in expected:
        row, current = (review.get("clips") or {}).get(cid) or {}, takes.get(cid) or {}
        verdict = row.get("verify") or {}
        if not current or row.get("video") != current.get("video") or row.get("take") != current.get("take"):
            return False
        if verdict.get("verdict") not in {"fine", "subtle"} or any(verdict.get(k) for k in ERROR_FIELDS):
            return False
        if row.get("story_ok") is False or row.get("technical") or row.get("flash_pending"):
            return False
    candidate = Path(assembly["final_video"])
    if candidate.parent != directory / CANDIDATE_DIR or not candidate.is_file():
        return False
    final = directory / f"{directory.name}.mp4"
    stat = candidate.stat()
    publication = directory / HISTORY_DIR / "publication.json"
    identity = [stat.st_size, stat.st_mtime_ns]
    if read(publication, {}).get("candidate") != identity:
        backup = directory / HISTORY_DIR / "previous_final.mp4"
        backup.parent.mkdir(parents=True, exist_ok=True)
        if final.is_file():
            copy_complete(final, backup)
        atomic_write_json(publication, {"candidate": identity})
    for key, suffix in (("cover", "_cover.jpeg"), ("ending", "_ending.jpeg"), ("ass", ".ass")):
        source = Path(assembly[key]) if assembly.get(key) else None
        if source is not None and source.is_file():
            target = directory / f"{directory.name}{suffix}"
            if source != target:
                copy_complete(source, target)
            assembly[key] = str(target)
    # Keep the candidate until its report is committed. A restart between the
    # movie replacement and report write can safely finish the same publication
    # without overwriting the previous movie's backup with the new movie.
    copy_complete(candidate, final)
    assembly.update(final_video=str(final), pending_publish=False, published_at=time.strftime("%F %T"))
    if assembly.get("media_qc"):
        qc = copy.deepcopy(assembly["media_qc"])
        for key in ("video", "cover", "ending", "ass"):
            if key in qc:
                qc[key] = str(final) if key == "video" else assembly.get(key, qc[key])
        assembly["media_qc"] = qc
        atomic_write_json(directory / "media_qc_report.json", qc)
    atomic_write_json(directory / "thin_media_report.json", media)
    try:
        candidate.unlink(missing_ok=True)
    except OSError:
        pass  # leftover staging media is harmless; publication is already committed
    return True
