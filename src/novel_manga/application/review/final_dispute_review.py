"""Run independent adjudication before publishing a same-movie finding correction."""
from __future__ import annotations
import copy
import time
from pathlib import Path
from novel_manga.util import atomic_write_json, read_json
from .final_dispute import records, attempted, apply
from .movie_context import same_evidence


def adjudicate(directory, cid, decision):
    """A repair keep requests fresh observation; it cannot supply its own verdict."""
    from . import final_cut, independent
    directory = Path(directory)
    media = read_json(directory/'thin_media_report.json', {})
    final = read_json(directory/'final_cut_review.json', {})
    if not final_cut.current(directory, media, final) or final.get('complete') is not True:
        raise ValueError('final dispute requires a complete current movie review')
    if any(attempted(r, cid) and same_evidence(r.get('context'), final['context']) for r in records(directory)):
        raise ValueError('final dispute already attempted for this clip and movie context')
    requested = decision.get('finding_ids')
    if requested is not None and (not isinstance(requested, list) or not requested):
        raise ValueError('finding_ids must name current disputed findings')
    issues = [i for i in final['issues'] if cid in i['clip_ids']
              and (requested is None or i.get('id') in requested)]
    if requested and set(requested) != {i.get('id') for i in issues}:
        raise ValueError('finding_ids must name current findings for this clip')
    # Picture-only observation cannot overrule measured speech/audio evidence.
    issues = [i for i in issues if not i.get('audio_evidence')]
    if not issues or any(not i.get('frame_paths') for i in issues):
        raise ValueError('final dispute needs actual current movie picture findings')
    if not decision.get('reason') or not decision.get('evidence'):
        raise ValueError('final dispute requires a reason and current picture evidence')
    target = directory/'work/final_disputes'/f'dispute-{time.time_ns()}'
    target.mkdir(parents=True)
    findings = [{'id': f'disputed-{i}', 'issue': copy.deepcopy(issue)} for i, issue in enumerate(issues, 1)]
    record = {'record': str(target/'record.json'), 'clip_id': cid, 'context': copy.deepcopy(final['context']),
              'decision': copy.deepcopy(decision), 'findings': findings,
              'original_review': copy.deepcopy(final), 'status': 'observing'}
    atomic_write_json(target/'record.json', record)
    dispute = {'reason': decision['reason'], 'evidence': decision['evidence'],
               'findings': [{**row['issue'], 'id': row['id']} for row in findings]}
    try:
        answer = independent.observe(directory, final, dispute=dispute)
        record['result'] = answer
        if answer.get('errors') or not same_evidence(answer.get('context'), final['context']):
            raise ValueError('independent dispute review failed or identified a different movie')
        independent.validate(answer, final['frames'], previous=dispute['findings'])
        resolved = {r['id'] for r in answer['prior_findings'] if r['status'] == 'resolved'}
        if any(i.get('prior_id', i.get('id')) in resolved for i in answer['issues']):
            raise ValueError('dispute reviewer both resolved and retained the same finding')
        if not final_cut.current(directory, read_json(directory/'thin_media_report.json', {}), final):
            raise ValueError('movie or approved inputs changed during final dispute review')
        record['status'] = 'complete'
        atomic_write_json(target/'record.json', record)
    except Exception as error:
        record.update(status='failed', error=str(error))
        atomic_write_json(target/'record.json', record)
        raise
    revised = apply(directory, final)
    atomic_write_json(directory/'final_cut_review.json', revised)
    review = final_cut.apply_feedback(directory, read_json(directory/'episode_review.json', {}), revised)
    atomic_write_json(directory/'episode_review.json', review)
    media['final_cut_review'] = revised
    atomic_write_json(directory/'thin_media_report.json', media)
    return {'review_changed': True, 'changed': [], 'dispute_record': str(target/'record.json')}
