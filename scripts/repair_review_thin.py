"""One precise verdict per current take, shared by repair, fill, audit and Flash confirmation."""
from __future__ import annotations

import argparse
import fcntl
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from novel_manga.review.reconciliation import evidence_key, missing_reviews
from review_store_thin import current_evidence, current_takes, reconcile
from verify_clips_thin import ROOT, Verifier, parse_episodes


class CurrentVerifier(Verifier):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("repair_advice", True)
        super().__init__(*args, **kwargs)

    def video_of(self, ep_dir, cid, review_clip):
        plan = self.load(ep_dir / "clip_plan.json") or {}
        review = self.load(ep_dir / "episode_review.json") or {}
        current = current_takes(ep_dir, plan, review).get(cid)
        return Path(current["video"]) if current else None


def review_batch(novel: Path, episodes: list[int], scope: str, state_dir: Path, legacy: Path, workers: int = 6, max_tokens: int | None = None) -> dict:
    cache = state_dir / "verified.jsonl"
    local, flash = current_evidence(legacy, state_dir)
    jobs = []
    for n in episodes:
        directory = novel / f"{novel.name}_{n}"
        review, takes = reconcile(directory, local, flash)
        for cid in missing_reviews(review, takes, scope):
            row = review["clips"][cid]
            claim = row.get("flash_pending") if scope == "flash" else row.get("story_issue", "")
            jobs.append((n, cid, str(claim or ""), "confirm" if scope == "flash" else "managed"))
    if jobs:
        verifier = CurrentVerifier(novel, cache, "local", workers, max_tokens=max_tokens)
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
        review, takes = reconcile(directory, local, flash)
        remaining += len(missing_reviews(review, takes, scope))
        from repair_history import observe, publish_if_ready
        observe(directory, review, takes)
        publish_if_ready(directory, review, takes)
    return {"episodes": episodes, "scope": scope, "judged": len(jobs), "remaining": remaining}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episodes", required=True)
    parser.add_argument("--scope", choices=["candidates", "changed", "all", "flash"], required=True)
    parser.add_argument('--max-tokens', type=int)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--legacy-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    from thin_batch import load_dotenv
    load_dotenv(ROOT / ".env")
    result = review_batch(args.novel_dir.resolve(), sorted(parse_episodes(args.episodes)), args.scope,
                          args.state_dir.resolve(), args.legacy_dir.resolve(), args.workers, args.max_tokens)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["remaining"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
