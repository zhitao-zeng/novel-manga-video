"""Count observable generated takes; never infer missing billing or generation history."""
from __future__ import annotations
import novel_manga.episodes as ep_names
from pathlib import Path
from collections import Counter
from ..util import read_json


def _take(path):
    if path.is_file() and path.stat().st_size:
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size)
    return None


def _path(value):
    return str(Path(value).resolve())


def materials(directory: Path, report: dict) -> list[dict]:
    candidates, aliases = [], {}
    for clip in report.get('clips', []):
        selected = clip.get('selected') or {}
        for row in [*clip.get('attempts', []), selected]:
            if row.get('video'):
                candidates.append({**row, 'selected': row['video'] == selected.get('video'), 'observed': True})
    history = read_json(directory / 'repair_history/history.json', {})
    for trial in history.get('trials', []):
        for before in trial.get('before', {}).values():
            archive = before.get('archive') or {}
            if archive.get('video'):
                original = archive.get('original_video') or archive['video']
                aliases[_path(archive['video'])] = _path(original)
                candidates.append({**(before.get('selected') or {}), 'video': archive['video'], 'observed': True})
        for render in trial.get('renders', []):
            for row in render.get('clips', {}).values():
                selected = row.get('selected') or {}
                for take in row.get('generated_takes', []):
                    candidates.append({**(selected if selected.get('video') == take.get('video') else {}),
                                       **take, 'observed': True})
    for folder in [directory / 'work/clips', directory / 'repair_history/takes']:
        if folder.is_dir():
            candidates.extend({'video': str(path)} for path in folder.rglob('clip*.mp4') if _take(path))

    rows, keys = [], {}
    for candidate in candidates:
        if not candidate.get('video'):
            continue
        path = Path(candidate['video'])
        actual = _take(path)
        recorded = tuple(candidate.get('take') or ()) or actual
        same = bool(actual) and recorded == actual
        if not actual and not candidate.get('observed'):
            continue
        task = read_json(Path(str(path) + '.task.json'), {}) if same else {}
        request = read_json(path.parent / 'request.json', {}) if same and path.name == 'clip.mp4' else {}
        asr_name = 'stale_asr.json' if path.name == 'clip.stale.mp4' else 'asr.json'
        asr = read_json(path.parent / asr_name, {}) if same else {}
        duration = candidate.get('duration')
        duration_source = 'report' if duration is not None else 'missing'
        if duration is None:
            if asr.get('duration') is not None:
                duration, duration_source = asr['duration'], 'analysis'
            else:
                duration = task.get('seconds', request.get('duration'))
                duration_source = 'request' if duration is not None else 'missing'
        provider = 'local_h3' if task.get('local') else 'phanrouter' if task.get('task_id') and task.get('model') else 'unknown'
        model = task.get('model') or 'unknown'
        resolution = str(task.get('resolution') or request.get('resolution') or 'unknown')
        usage = task.get('usage') or {}
        tokens = usage.get('total_tokens')
        task_key = (provider, task.get('endpoint', '') if provider == 'local_h3' else '', task['task_id']) if task.get('task_id') else None
        media_key = ('file', aliases.get(_path(path), _path(path)), recorded)
        old_index = keys.get(task_key, keys.get(media_key))
        row = {'provider': provider, 'model': model, 'resolution': resolution,
               'seconds': float(duration) if duration is not None else None,
               'duration_source': duration_source,
               'tokens': int(tokens) if tokens is not None else None,
               'task_id': task.get('task_id'), 'selected': bool(candidate.get('selected')),
               'identity': task_key or media_key}
        if old_index is None:
            old_index = len(rows); rows.append(row)
        else:
            previous = rows[old_index]
            for name in ('provider', 'model', 'resolution', 'seconds', 'tokens', 'task_id'):
                if previous[name] in ('unknown', None) and row[name] not in ('unknown', None):
                    previous[name] = row[name]
            previous['selected'] |= row['selected']
            if (previous['duration_source'] == 'missing' and row['duration_source'] != 'missing'
                    or previous['duration_source'] == 'request' and row['duration_source'] in ('report', 'analysis')):
                previous['seconds'], previous['duration_source'] = row['seconds'], row['duration_source']
            if task_key:
                previous['identity'] = task_key
        keys[media_key] = old_index
        if task_key:
            keys[task_key] = old_index
    return rows


def group_usage(rows: list[dict]) -> list[dict]:
    groups = {}
    for row in rows:
        key = (row['provider'], row['model'], row['resolution'])
        group = groups.setdefault(key, {'provider': key[0], 'model': key[1], 'resolution': key[2],
                'attempts': 0, 'seconds': 0.0, 'tokens': 0, 'missing_seconds': 0, 'missing_tokens': 0, 'without_task_id': 0,
                'duration_from_request': 0})
        group['attempts'] += 1
        group['seconds'] += row['seconds'] or 0
        group['tokens'] += row['tokens'] or 0
        group['missing_seconds'] += row['seconds'] is None
        group['missing_tokens'] += row['tokens'] is None
        group['without_task_id'] += not bool(row['task_id'])
        group['duration_from_request'] += row['duration_source'] == 'request'
    return [dict(group, seconds=round(group['seconds'], 3)) for _, group in sorted(groups.items())]


def episode_rows(novel_dir: Path, chapters: set[int] | None = None) -> list[dict]:
    rows = []
    if not novel_dir.is_dir():
        return rows
    for directory in sorted(novel_dir.glob(f'{novel_dir.name}_*')):
        if not directory.is_dir() or not ep_names.is_episode(directory.name):
            continue
        chapter = ep_names.chapter_of(directory.name)
        if chapters is not None and chapter not in chapters:
            continue
        report = read_json(directory / 'thin_media_report.json', {})
        observed = materials(directory, report)
        if not report and not observed:
            continue
        groups = group_usage(observed)
        resolutions = {row['resolution'] for row in observed}
        assembly = report.get('assembly') or {}
        rows.append({'episode': directory.name, 'chapter': chapter,
            'resolution': next(iter(resolutions)) if len(resolutions) == 1 else 'mixed' if resolutions else 'unknown',
            'video_seconds': round(sum(r['seconds'] or 0 for r in observed), 3),
            'video_tokens': sum(r['tokens'] or 0 for r in observed),
            'discarded_seconds': round(sum(r['seconds'] or 0 for r in observed if not r['selected']), 3),
            'selected_seconds': round(sum(r['seconds'] or 0 for r in observed if r['selected']), 3),
            'clips': len(report.get('clips', [])), 'attempts': len(observed),
            'planner_calls': len(list(directory.glob('request_attempt_*.json'))),
            'judge_clips': len(read_json(directory / 'episode_review.json', {}).get('clips', {})),
            'final_seconds': float(assembly.get('duration') or 0), 'status': report.get('status', ''),
            'groups': groups, 'materials': observed})
    return rows


def image_records(novel_dir: Path):
    assets = novel_dir / 'series_assets'
    if assets.is_symlink():
        return [], assets.resolve().parent.name
    rows, seen = [], set()
    for path in assets.rglob('*.task.json') if assets.is_dir() else []:
        image = Path(str(path)[:-len('.task.json')])
        if not _take(image):
            continue
        task = read_json(path, {})
        key = ('image', task['task_id']) if task.get('task_id') else ('file', _path(image))
        if key in seen:
            continue
        seen.add(key); rows.append({'model': task.get('model') or 'unknown', 'task_id': task.get('task_id')})
    return rows, ''


def summarize(novel_dir: Path, rates: dict | None = None, chapters: set[int] | None = None):
    rates = rates or {}
    episodes = episode_rows(novel_dir, chapters)
    unique = {}
    for episode in episodes:
        for row in episode['materials']:
            previous = unique.setdefault(row['identity'], dict(row))
            previous['selected'] |= row['selected']
    groups = group_usage(list(unique.values()))
    images, shared_with = image_records(novel_dir)
    known_cost, unpriced = 0.0, 0
    for group in groups:
        cost = None
        if group['provider'] == 'local_h3':
            group['api_cost'] = 0.0
            continue
        if group['provider'] == 'phanrouter':
            token_rate = (rates.get('video_per_million_tokens') or {}).get(group['model'])
            by_model = (rates.get('video_per_second') or {}).get(group['model'])
            second_rate = (by_model if isinstance(by_model, dict) else rates.get('video_per_second', {})).get(group['resolution'])
            if token_rate and not group['missing_tokens']:
                cost = group['tokens'] * float(token_rate) / 1_000_000
            elif second_rate and not group['missing_seconds']:
                cost = group['seconds'] * float(second_rate)
        group['api_cost'] = round(cost, 6) if cost is not None else None
        unpriced += cost is None
        known_cost += cost or 0
    image_rate = rates.get('image_per_card')
    image_cost = len(images) * float(image_rate) if image_rate else 0.0 if not images else None
    known_cost += image_cost or 0
    unpriced += image_cost is None
    return {'novel': novel_dir.name, 'episodes': len(episodes),
            'video_seconds': round(sum(g['seconds'] for g in groups), 3),
            'video_tokens': sum(g['tokens'] for g in groups),
            'final_seconds': round(sum(r['final_seconds'] for r in episodes), 3),
            'selected_seconds': round(sum(r['selected_seconds'] for r in episodes), 3),
            'discarded_seconds': round(sum(r['seconds'] or 0 for r in unique.values() if not r['selected']), 3),
            'images': len(images), 'shared_assets': shared_with, 'groups': groups,
            'image_models': dict(Counter(r['model'] for r in images)),
            'cost': None if unpriced else round(known_cost, 6), 'known_api_cost': round(known_cost, 6),
            'currency': rates.get('currency', ''), 'unpriced_groups': unpriced,
            'scope': 'available_generation_records', 'history_complete': False,
            'local_compute_cost': None, 'image_scope': 'whole_novel',
            'coverage': {'missing_seconds': sum(g['missing_seconds'] for g in groups),
                         'missing_tokens': sum(g['missing_tokens'] for g in groups),
                         'duration_from_request': sum(g['duration_from_request'] for g in groups),
                         'without_task_id': sum(g['without_task_id'] for g in groups)}}
