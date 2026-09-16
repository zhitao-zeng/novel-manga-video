"""Small same-take, confirmation and publication-facing review fixtures."""
import copy
from novel_manga.review.contracts import POLICY


def cases():
    current = {'video': '/takes/current.mp4', 'take': [10, 100, 1000]}
    old = {'video': '/takes/old.mp4', 'take': [9, 90, 900]}
    plan = {'clips': [{'clip_id': 'a', 'kind': 'video'}, {'clip_id': 'title', 'kind': 'title'}]}
    initial = {'policy': POLICY, 'episode': 'book_1', 'clips': {
        'a': {**current, 'severity': 'fail', 'tier': 'must_fix', 'feedback': 'original note'}}, 'feedback': {'a': 'original note'}}
    record = {**current, 'ep': 1, 'clip': 'a', 'verdict': 'fine', 'mode': 'all', 'people': ['甲(男) 站立'], 'evidence': 'current picture', 'instruction': ''}
    rows = []
    def add(name, previous=None, local=None, flash=None, takes=None):
        rows.append(copy.deepcopy({'name': name, 'plan': plan, 'previous': initial if previous is None else previous,
                    'takes': {'a': current} if takes is None else takes, 'local': local or [], 'flash': flash or []}))
    add('first_precise_pass', local=[record])
    add('old_take_does_not_clear_current', local=[{**record, **old}])
    add('replacement_needs_review', takes={'a': old}, local=[record])
    verified = {**initial, 'clips': {'a': {**initial['clips']['a'], 'verify': {'verdict': 'obvious'}, 'story_ok': False}}}
    add('ordinary_scan_keeps_prior_precise_verdict', verified, [record])
    add('joint_recheck_replaces_ordinary', verified, [{**record, 'mode': 'joint'}])
    add('flash_candidate_awaits_confirmation', local=[record], flash=[{**record, 'verdict': 'obvious', 'evidence': 'possible duplicate'}])
    add('explicit_confirmation_clears_flash', local=[{**record, 'mode': 'confirm'}], flash=[{**record, 'verdict': 'obvious'}])
    confirmed = copy.deepcopy(verified); confirmed['clips']['a']['flash_checked'] = True
    add('source_recheck_overrides_confirmation', confirmed, [{**record, 'mode': 'source_confirm', 'source_confirmed_at': 'fixed-time'}])
    technical = copy.deepcopy(initial);technical['clips']['a']['technical'] = True
    add('technical_failure_stays', technical, [record])
    gate = copy.deepcopy(verified);gate['clips']['a'].update(tier='optional', verified={'verdict': 'fine'})
    add('old_final_gate_with_evidence', gate, [record])
    add('old_final_gate_without_evidence', gate)
    removed = copy.deepcopy(initial);removed['clips']['removed'] = {'tier': 'must_fix', 'feedback': 'outdated clip'}
    add('removed_id_cannot_request_retake', removed, [record])
    add('missing_media_stays_unchecked', takes={})
    add('source_confirmation_has_priority', local=[{**record, 'mode': 'source_confirm', 'source_confirmed_at': 'fixed-time'}, {**record, 'mode': 'joint', 'verdict': 'obvious'}, {**record, 'mode': 'confirm', 'verdict': 'obvious'}])
    return rows
