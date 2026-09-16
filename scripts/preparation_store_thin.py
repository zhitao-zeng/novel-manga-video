"""Existing preparation reports, input stamps, backups and resume eligibility."""
from __future__ import annotations

from pathlib import Path
from collections import Counter
import json
import os
import time
from novel_manga.util import atomic_write_json
from novel_manga.planning.preparation import POLICY, TERMINAL

def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def inputs(directory):
    from thin_profile import plan_fingerprint
    from identity_store_thin import data_files
    plan = read(directory / 'clip_plan.json', {})
    paths = [directory / 'chapter_script.json', directory / 'segments.json',
             directory / 'identity_context.json', *data_files(directory.parent)]
    return {'files': {str(p): [p.stat().st_mtime_ns, p.stat().st_size] if p.is_file() else None for p in paths},
            'plan': plan_fingerprint(plan)}


def has_video(directory):
    return (directory / (directory.name + '.mp4')).is_file() or any((directory / 'work/clips').glob('*/attempt_*/clip.mp4'))


def backup(directory):
    dest = directory.parent / 'h3_preparation/before' / directory.name.rsplit('_', 1)[-1]
    dest.mkdir(parents=True, exist_ok=True)
    for name in ['chapter_script.json', 'clip_plan.json', 'chapter_script_report.json',
                 'segments.json', 'episode_plan.json', 'review_feedback.json', 'source_speaker_contract.json']:
        source = directory / name
        if source.is_file() and not (dest / name).exists():
            (dest / name).write_bytes(source.read_bytes())


def record(directory, status, **extra):
    p = directory.parent / 'h3_preparation/episodes' / (directory.name.rsplit('_', 1)[-1] + '.json')
    old = read(p, {})
    if status == 'starting':
        old = {}
    row = {**old, 'policy': POLICY, 'episode': int(directory.name.rsplit('_', 1)[-1]),
           'status': status, 'at': time.strftime('%F %T'), 'inputs': inputs(directory), **extra}
    row.pop('retry_after', None)
    if status in {'error', 'needs_repair', 'needs_replan'} and row.get('attempts', 0) < 3:
        row['retry_after'] = time.time() + 60 * max(1, row.get('attempts', 1))
    atomic_write_json(p, row)
    return row


def eligible(row, directory):
    if row.get('policy') != POLICY or row.get('inputs') != inputs(directory):
        return True
    if row.get('status') not in TERMINAL:
        return True
    return bool(row.get('retry_after') and row.get('attempts', 0) < 3)


def summary(novel, chapters, status, running=None, workers=None):
    rows = [read(novel / 'h3_preparation/episodes' / f'{n}.json', {'episode': n, 'status': 'pending'}) for n in chapters]
    result = {'policy': POLICY, 'at': time.strftime('%F %T'), 'pid': os.getpid(), 'status': status,
              'total': len(chapters), 'counts': dict(Counter(r['status'] for r in rows)),
              'running': running or {}, 'chapters': chapters, 'workers': workers}
    atomic_write_json(novel / 'h3_preparation/status.json', result)
    return result


