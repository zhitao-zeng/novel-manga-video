"""One independent, picture-grounded adjudication per clip and actual movie context."""
from __future__ import annotations
import copy
import time
from pathlib import Path

from novel_manga.util import atomic_write_json, read_json


def records(directory):
    return [read_json(path, {}) for path in sorted((Path(directory)/'work/final_disputes').glob('*/record.json'))]


def attempted(record, cid):
    return (record.get('clip_id') == cid or
            any(cid in r['issue']['clip_ids'] for r in record.get('findings', [])))


def available(directory, cid):
    """Picture adjudication has its own single-use allowance, never a video grant."""
    from .movie_context import context
    directory = Path(directory)
    final = read_json(directory/'final_cut_review.json', {})
    if final.get('complete') is not True or not any(
            cid in i.get('clip_ids', []) and i.get('frame_paths') and not i.get('audio_evidence')
            for i in final.get('issues', [])):
        return False
    return (not any(attempted(r, cid) and r.get('context') == final.get('context') for r in records(directory))
            and final.get('context') == context(directory, read_json(directory/'thin_media_report.json', {}), final.get('movie', '')))


def apply(directory, final):
    """Apply current adjudications without rewriting either original reviewer report."""
    relevant = [r for r in records(directory) if r.get('status') == 'complete'
                and r.get('context') == final.get('context')]
    if not relevant:
        return final
    result = copy.deepcopy(final)
    original = result.get('pre_dispute_review') or {
        k: copy.deepcopy(result[k]) for k in ('complete', 'passed', 'issues', 'errors')}
    result.update(copy.deepcopy(original))
    result['pre_dispute_review'] = original
    result['dispute_reviews'] = []
    for record in relevant:
        answer = record['result']
        resolved = {r['id'] for r in answer['prior_findings'] if r['status'] == 'resolved'}
        removed = [r['issue'] for r in record['findings'] if r['id'] in resolved]
        result['issues'] = [i for i in result['issues'] if i not in removed]
        previous = {r['id'] for r in record['findings']}
        for issue in answer['issues']:
            # A still-present disputed finding already remains in the original report.
            if issue.get('prior_id', issue.get('id')) in previous:
                continue
            if issue not in result['issues']:
                result['issues'].append(copy.deepcopy(issue))
        result['dispute_reviews'].append({'record': record['record'], 'clip_id': record['clip_id'],
                                          'prior_findings': copy.deepcopy(answer['prior_findings'])})
    result['passed'] = bool(result['complete'] and not result['issues'] and not result['errors'])
    return result


def relocate(directory, final):
    """Carry an adjudication across the existing controlled movie-copy publication."""
    for item in final.get('dispute_reviews', []):
        original = read_json(Path(item['record']), {})
        if not original or original.get('status') != 'complete':
            raise ValueError('published movie lacks its original dispute adjudication')
        if original['context'] == final['context']:
            continue
        target = Path(directory)/'work/final_disputes'/f'publication-{time.time_ns()}'/'record.json'
        relocated = {**original, 'context': copy.deepcopy(final['context']),
                     'record': str(target), 'relocated_from': item['record']}
        atomic_write_json(target, relocated)
        item['record'] = str(target)
