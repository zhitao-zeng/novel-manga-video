"""Confirmed cloud-media quota stops, interoperable with the interactive resource store."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import time

from novel_manga.util import atomic_write_json


def group(model):
    if model in {'MiniMax-H3', 'sd2.0', 'sd2.0-fast', 'sd2.0-mini', 'gpt-image-2',
                 'doubao-seedream-5.0-lite', 'doubao-seedream-4.5'}:
        return 'router-image'
    if model == 'sd2.5':
        return 'router-video'
    return None


def marker(model, key):
    if not key or group(model) is None:
        return None
    root = Path(os.environ.get('NOVEL_SHARED_QUOTA_DIR') or '/mnt/disk1/zengzhitao/tmp/resource-quota')
    # Reuse the interactive private-token namespace instead of querying the
    # provider's token identity at every admission. Key rotation changes the stop.
    # Neither credential values nor this private identifier go into logs or Git.
    return root/'phanrouter'/(hashlib.sha256(key.encode()).hexdigest()+'.json')


def check(model, key):
    path = marker(model, key)
    if path is None or not path.is_file():
        return
    row = json.loads(path.read_text())
    if row.get('exhausted') and (not row.get('resets_at_epoch') or row['resets_at_epoch'] > time.time()):
        raise RuntimeError(f'quota_not_enough: resource quota exhausted: {group(model)}; refresh account evidence before reuse')


def remember(model, key, status, body):
    path = marker(model, key)
    if path is None or status not in {402, 403, 429} or not isinstance(body, dict):
        return False
    error = body.get('error', body)
    if not isinstance(error, dict) or error.get('code') not in {'quota_not_enough', 'insufficient_quota', 'insufficient_credits'}:
        return False
    if error['code'] == 'insufficient_credits' and error.get('available_points') not in (None, 0):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = json.loads(path.read_text()) if path.is_file() else {}
        history = previous.get('history', [])
        if previous:
            history = [*history, {k:v for k,v in previous.items() if k != 'history'}]
        row = {'group': group(model), 'exhausted': True, 'code': error['code'], 'status': status,
               'observed_at_epoch': time.time(), 'history': history}
        if error.get('resets_at_epoch'):
            row['resets_at_epoch'] = error['resets_at_epoch']
        atomic_write_json(path, row)
    return True
