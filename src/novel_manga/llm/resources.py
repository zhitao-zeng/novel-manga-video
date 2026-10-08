"""Configured model endpoints share file-lock slots across host processes."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import time
from urllib.parse import quote, urlsplit


def _endpoint(base):
    parsed = urlsplit(base)
    host = parsed.hostname
    if not host or parsed.scheme not in {'http', 'https'}:
        raise ValueError('model resource base must be an HTTP endpoint')
    try:
        host = ipaddress.ip_address(host).compressed
    except ValueError:
        host = host.lower()
    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    except ValueError as error:
        raise ValueError('model resource endpoint has an invalid port') from error
    return f'{quote(host, safe=".-_")}_{port}'


def _pool(base_url):
    raw = os.environ.get('NOVEL_MODEL_RESOURCES', '').strip()
    if not raw:
        return None
    try:
        rows = json.loads(raw)
    except ValueError as error:
        raise ValueError('NOVEL_MODEL_RESOURCES must be a JSON list of base/slots objects') from error
    if not isinstance(rows, list):
        raise ValueError('NOVEL_MODEL_RESOURCES must be a JSON list of base/slots objects')
    if not rows:
        return None
    target = _endpoint(base_url)
    groups = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('base'), str):
            raise ValueError('each model resource needs a base endpoint and slots')
        slots = row.get('slots')
        if not isinstance(slots, int) or isinstance(slots, bool) or slots <= 0:
            raise ValueError('model resource slots must be a positive integer')
        members = [_endpoint(base.strip()) for base in row['base'].split(',') if base.strip()]
        if not members:
            raise ValueError('model resource group needs at least one endpoint')
        groups.append((members, slots))
    # A local resource may name several failover endpoints with one total cap.
    # Overlapping aliases belong to the same group, using the smallest allowance.
    shared = {target}
    while True:
        before = len(shared)
        for members, _ in groups:
            if shared.intersection(members):
                shared.update(members)
        if len(shared) == before:
            break
    matched = [(members, slots) for members, slots in groups if shared.intersection(members)]
    if not matched:
        return None
    name = matched[0][0][0]
    capacity = min(slots for _, slots in matched)
    root = os.environ.get('NOVEL_MODEL_INFLIGHT_DIR', '').strip()
    if not root:
        raise ValueError('NOVEL_MODEL_INFLIGHT_DIR is required for configured model resources')
    return Path(root) / name, capacity


@contextmanager
def model_slot(base_url, *, deadline=None, on_wait=None, cancelled=None, reserve=0, record_queue=True):
    """Hold one endpoint slot, with an optional absolute monotonic wait deadline.

    Unconfigured endpoints keep ordinary behavior. A supplied callback runs once
    when acquisition must wait. An optional cancellation predicate interrupts
    admission, never work that already owns a slot. The caller owns requests
    and retry decisions.
    """
    pool = _pool(base_url)
    if pool is None:
        if cancelled is not None and cancelled():
            raise InterruptedError('model resource admission cancelled')
        yield None
        return
    if type(reserve) is not int or reserve < 0:
        raise ValueError('reserved model slots must be a nonnegative integer')
    directory, capacity = pool
    directory.mkdir(parents=True, exist_ok=True)
    waiting = False
    from novel_manga.resource_waits import QueueWait
    with QueueWait('model',enabled=record_queue) as queue:
        while True:
            if cancelled is not None and cancelled():
                raise InterruptedError('model resource admission cancelled')
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('model resource wait budget exhausted')
            effective = capacity
            try:
                effective = max(1, min(capacity, int((directory / 'limit').read_text().strip())))
            except (OSError, ValueError):
                pass
            # Long Agent sessions use the same lock pool, but leave its final slots
            # available to short input checks, translation and independent review.
            effective = max(1, effective-reserve)
            for index in range(effective):
                handle = (directory / f'slot_{index:02d}.lock').open('a+')
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    handle.close()
                    continue
                try:
                    queue.finish()
                    yield handle
                finally:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_UN)
                    finally:
                        handle.close()
                return
            if not waiting:
                queue.begin()
                waiting = True
                if on_wait is not None:
                    on_wait()
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError('model resource wait budget exhausted')
            time.sleep(0.1 if remaining is None else min(0.1, remaining))
