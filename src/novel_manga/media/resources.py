"""Cross-process video admission with ordered, process-owned waiters."""
from __future__ import annotations
import fcntl
import os
import threading
import time
from pathlib import Path


def _earlier_capacity(directory, ticket):
    """Older live callers own first refusal on slots within their ceiling.

    A caller with a lower ceiling need not block an otherwise free higher slot.
    A dead worker's ticket is removed using its released lock, without PID guesses.
    The admission lock covers creating, inspecting and removing tickets.
    """
    ceiling = 0
    for path in sorted(directory.glob('wait_*.lock')):
        if path.name >= ticket.name:
            break
        with path.open('r+') as waiter:
            try:
                fcntl.flock(waiter, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                ceiling = max(ceiling, int(waiter.read()))
            else:
                path.unlink()
    return ceiling

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
    from novel_manga.resource_waits import QueueWait
    with QueueWait('video') as queue, (directory/'admission.lock').open('a+') as admission:
        queue.begin()
        fcntl.flock(admission, fcntl.LOCK_EX)
        # First videos take the next free slot ahead of queued repairs. Existing
        # holders keep running; equal-priority callers retain their FIFO order.
        # Legacy live tickets still contain the same integer ceiling and drain.
        stamp=f'{time.monotonic_ns():020d}'
        if os.environ.get('NOVEL_VIDEO_PRIORITY')=='first':
            stamp='00000000000000000000_'+stamp
        ticket = directory/f'wait_{stamp}_{os.getpid()}_{threading.get_ident()}.lock'
        waiter = ticket.open('x+')
        fcntl.flock(waiter, fcntl.LOCK_EX)
        waiter.write(str(limit))
        waiter.flush()
        fcntl.flock(admission, fcntl.LOCK_UN)
        try:
            while True:
                fcntl.flock(admission, fcntl.LOCK_EX)
                try:
                    # The conductor may lower its cap live; this caller's limit
                    # remains its ceiling. Keep the same slot files for holders.
                    effective = limit
                    try:
                        effective = max(1, min(limit, int((directory/'limit').read_text().strip() or limit)))
                    except (OSError, ValueError):
                        pass
                    first = _earlier_capacity(directory, ticket)
                    for index in range(first, effective):
                        handle = (directory/f'slot_{index:02d}.lock').open('a+')
                        try:
                            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            handle.close()
                            continue
                        ticket.unlink()
                        queue.finish()
                        return handle
                finally:
                    fcntl.flock(admission, fcntl.LOCK_UN)
                time.sleep(0.1)
        finally:
            fcntl.flock(admission, fcntl.LOCK_EX)
            ticket.unlink(missing_ok=True)
            waiter.close()
            fcntl.flock(admission, fcntl.LOCK_UN)

def release_inflight_slot(handle) -> None:
    if handle is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()
