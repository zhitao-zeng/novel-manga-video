"""Run one repair review batch, then observe history and attempt existing publication."""
from __future__ import annotations
import fcntl
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from novel_manga.review.reconciliation import evidence_key, missing_reviews
import review_store_thin as review_store
import verify_clips_thin as verify_clips

def review_batch(novel: Path, episodes: list[int], scope: str, state_dir: Path, legacy: Path, workers: int = 6, max_tokens: int | None = None) -> dict:
    cache = state_dir / "verified.jsonl"
    local, flash = review_store.current_evidence(legacy, state_dir)
    jobs = []
    for n in episodes:
        directory = novel / f"{novel.name}_{n}"
        review, takes = review_store.reconcile(directory, local, flash)
        for cid in missing_reviews(review, takes, scope):
            row = review["clips"][cid]
            claim = row.get("flash_pending") if scope == "flash" else row.get("story_issue", "")
            jobs.append((n, cid, str(claim or ""), "confirm" if scope == "flash" else "managed"))
    if jobs:
        verifier = verify_clips.CurrentVerifier(novel, cache, "local", workers, max_tokens=max_tokens)
        cache.parent.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for record in pool.map(verifier.verify, jobs):
                with cache.open("a", encoding="utf-8") as stream:
                    fcntl.flock(stream, fcntl.LOCK_EX)
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
                if "error" not in record:
                    local[evidence_key(record["ep"], record["clip"], record["video"], record["take"])] = record
    remaining = 0
    for n in episodes:
        directory = novel / f"{novel.name}_{n}"
        review, takes = review_store.reconcile(directory, local, flash)
        remaining += len(missing_reviews(review, takes, scope))
        from repair_history import observe, publish_if_ready
        observe(directory, review, takes)
        publish_if_ready(directory, review, takes)
    return {"episodes": episodes, "scope": scope, "judged": len(jobs), "remaining": remaining}
