"""Video admission order and capacity using actual cross-process locks."""
from contextlib import contextmanager, ExitStack
import os
import fcntl
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

from novel_manga.media.resources import acquire_inflight_slot, release_inflight_slot


CHILD = '''
import sys
from pathlib import Path
from novel_manga.media.resources import acquire_inflight_slot, release_inflight_slot
slot = acquire_inflight_slot(Path(sys.argv[1]), int(sys.argv[2]))
print('ACQUIRED', flush=True)
sys.stdin.readline()
release_inflight_slot(slot)
'''


@contextmanager
def child(directory, limit, priority=None):
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'src')}
    if priority is not None:
        env['NOVEL_VIDEO_PRIORITY']=priority
    proc = subprocess.Popen([sys.executable, '-c', CHILD, str(directory), str(limit)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=3)


def acquired(proc):
    assert select.select([proc.stdout], [], [], 3)[0], proc.poll()
    assert proc.stdout.readline().strip() == 'ACQUIRED'


def waiting(directory, proc):
    deadline = time.monotonic()+3
    while not list(directory.glob(f'wait_*_{proc.pid}_*.lock')):
        assert proc.poll() is None
        assert time.monotonic() < deadline
        time.sleep(.01)


def release(proc):
    proc.stdin.write('\n'); proc.stdin.flush()
    assert proc.wait(timeout=3) == 0


@pytest.fixture
def pool(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_INFLIGHT_DIR', str(tmp_path))
    return tmp_path


def test_waiters_get_slots_in_order_across_processes(pool):
    held = acquire_inflight_slot(pool, 1)
    with ExitStack() as stack:
        processes = []
        for _ in range(4):
            proc = stack.enter_context(child(pool, 1))
            waiting(pool, proc)
            processes.append(proc)
        release_inflight_slot(held)
        for index, proc in enumerate(processes):
            acquired(proc)
            assert not any(select.select([p.stdout], [], [], .01)[0] for p in processes[index+1:])
            release(proc)
    assert not list(pool.glob('wait_*.lock'))


def test_killed_waiter_does_not_hold_back_later_work(pool):
    held = acquire_inflight_slot(pool, 1)
    with child(pool, 1) as dead:
        waiting(pool, dead)
        dead.kill(); dead.wait(timeout=3)
        with child(pool, 1) as live:
            waiting(pool, live)
            release_inflight_slot(held)
            acquired(live)
            release(live)
    assert not list(pool.glob('wait_*.lock'))


def test_lower_ceiling_waiter_leaves_higher_free_slot_available(pool):
    held = acquire_inflight_slot(pool, 1)
    with child(pool, 1) as narrow:
        waiting(pool, narrow)
        with child(pool, 2) as wider:
            acquired(wider)
            assert not select.select([narrow.stdout], [], [], .05)[0]
            release_inflight_slot(held)
            acquired(narrow)
            release(wider); release(narrow)


def test_live_lowered_capacity_preserves_existing_holders(pool):
    first = acquire_inflight_slot(pool, 2)
    second = acquire_inflight_slot(pool, 2)
    (pool/'limit').write_text('1')
    with child(pool, 2) as waiter:
        waiting(pool, waiter)
        release_inflight_slot(second)
        assert not select.select([waiter.stdout], [], [], .2)[0]
        release_inflight_slot(first)
        acquired(waiter)
        release(waiter)


def test_zero_limit_is_unrestricted_and_creates_no_queue(pool):
    assert acquire_inflight_slot(pool, 0) is None
    assert not list(pool.iterdir())


def test_first_video_precedes_older_queued_repair_without_interrupting_holder(pool):
    held=acquire_inflight_slot(pool,1)
    with child(pool,1,'repair') as repair:
        waiting(pool,repair)
        with child(pool,1,'first') as first:
            waiting(pool,first)
            assert not select.select([first.stdout,repair.stdout],[],[],.1)[0]
            release_inflight_slot(held)
            acquired(first)
            assert not select.select([repair.stdout],[],[],.1)[0]
            release(first)
            acquired(repair);release(repair)


def test_first_video_precedes_live_legacy_waiter_during_rollout(pool):
    held=acquire_inflight_slot(pool,1)
    legacy=pool/f'wait_{time.monotonic_ns():020d}_legacy.lock'
    with legacy.open('w+') as waiter:
        waiter.write('1');waiter.flush();fcntl.flock(waiter,fcntl.LOCK_EX)
        with child(pool,1,'first') as first:
            waiting(pool,first)
            release_inflight_slot(held)
            acquired(first);release(first)
        assert legacy.exists()
