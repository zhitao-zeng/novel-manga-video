"""The existing file-lock semaphore; no scheduling or capacity changes."""
from __future__ import annotations
import fcntl
import os
import time
from pathlib import Path

def acquire_inflight_slot(novel_dir: Path, limit: int):
    """A cross-process counting semaphore made of lock files: every runner of the
    novel competes for the same `limit` slots, so the clips in flight stay at a
    constant number no matter how many episodes render at once."""
    if limit <= 0:
        return None
    # A second lane (another model behind another key, with its own concurrency
    # allowance) names its pool with NOVEL_INFLIGHT_POOL so the two caps do not
    # share one set of slots.
    pool = os.environ.get("NOVEL_INFLIGHT_POOL", "").strip()
    # NOVEL_INFLIGHT_DIR makes the pool belong to the API key rather than to this novel, so
    # two novels rendering on one key share a single ceiling instead of one each.
    shared = os.environ.get("NOVEL_INFLIGHT_DIR", "").strip()
    directory = Path(shared) if shared else novel_dir / (f".inflight-{pool}" if pool else ".inflight")
    directory.mkdir(parents=True, exist_ok=True)
    while True:
        # A `limit` file in the pool (the conductor's AIMD on 429s) lowers the
        # cap live; --inflight stays the ceiling.
        effective = limit
        try:
            effective = max(1, min(limit, int((directory / "limit").read_text(encoding="utf-8").strip() or limit)))
        except (OSError, ValueError):
            pass
        for index in range(effective):
            handle = open(directory / f"slot_{index:02d}.lock", "w")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return handle
            except OSError:
                handle.close()
        time.sleep(3)

def release_inflight_slot(handle) -> None:
    if handle is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()
