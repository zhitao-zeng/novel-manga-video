"""Frozen pre-extraction review outputs and boundaries between rules and storage."""
import copy
import json
from pathlib import Path
import pytest
from novel_manga.review import reconciliation as rules
from review_reconciliation_cases import cases
import novel_manga.application.review.store as store

EXPECTED = {r['name']: r for r in json.loads((Path(__file__).parent / 'fixtures/review_reconciliation_before.json').read_text())}


@pytest.mark.parametrize('case', cases(), ids=lambda case: case['name'])
def test_same_inputs_keep_report_scope_and_counts(case):
    before = copy.deepcopy(case)
    local, flash = {}, {}
    for record in case['local']:
        rules.merge_evidence(local, record)
    for record in case['flash']:
        rules.merge_evidence(flash, record)
    inputs = (Path('/frozen/book/book_1'), case['plan'], case['previous'], case['takes'], local, flash)
    report = rules.reconcile_review(*inputs)
    actual = {'name': case['name'], 'report': report, 'takes': case['takes'],
              'counts': rules.inspection_counts(report, case['takes'], ['a']),
              'missing': {scope: rules.missing_reviews(report, case['takes'], scope)
                          for scope in ['all', 'changed', 'candidates', 'flash']}}
    assert actual == EXPECTED[case['name']]
    assert case == before
    assert rules.reconcile_review(*inputs) == report
    assert rules.reconcile_review(inputs[0], case['plan'], report, case['takes'], local, flash) == report


def test_partial_jsonl_and_model_errors_do_not_replace_confirmed_evidence(tmp_path):
    current = cases()[0]['local'][0]
    path = tmp_path / 'verified.jsonl'
    confirmed = {**current, 'mode': 'confirm'}
    path.write_text(json.dumps(confirmed) + '\n' + json.dumps({**current, 'verdict': 'obvious'}) + '\n'
                    + json.dumps({**current, 'mode': 'source_confirm', 'error': 'timeout'}) + '\n{"unfinished":')
    assert list(store.load_evidence([tmp_path / 'missing.jsonl', path]).values()) == [confirmed]


@pytest.mark.parametrize('policy_current',[True,False])
def test_reconciliation_retains_mode_only_from_current_policy(policy_current):
    from novel_manga.review.contracts import POLICY
    case=cases()[0]
    previous={**case['previous'],'policy':POLICY if policy_current else 'old-policy','review_mode':'verify'}
    result=rules.reconcile_review(Path('/frozen/book/book_1'),case['plan'],previous,case['takes'],{}, {})
    assert (result.get('review_mode')=='verify') is policy_current


def test_source_confirm_from_another_mode_cannot_keep_the_old_mode_stamp():
    from novel_manga.review.contracts import POLICY,current_review
    case=cases()[0]
    previous={**case['previous'],'policy':POLICY,'review_mode':'verify'}
    record={**case['local'][0],'mode':'source_confirm','source_confirmed_at':'new',
            'policy':POLICY,'review_mode':'classic',
            'picture_review':{'severity':'pass','story_ok':True}}
    local={};rules.merge_evidence(local,record)
    result=rules.reconcile_review(Path('/frozen/book/book_1'),case['plan'],previous,case['takes'],local,{})
    assert not current_review(result,'verify')


def test_source_priority_does_not_hide_a_fresh_requested_review_method():
    from novel_manga.review.contracts import POLICY
    record=cases()[0]['local'][0]
    classic={**record,'mode':'source_confirm','policy':POLICY,'review_mode':'classic'}
    verify={**record,'mode':'managed','policy':POLICY,'review_mode':'verify'}
    records={}
    rules.merge_evidence(records,classic);rules.merge_evidence(records,verify)
    assert list(records.values())==[verify]


def test_current_take_selection_keeps_project_relative_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    episode = tmp_path / 'outputs/n/n_1'; episode.mkdir(parents=True)
    selected = episode / 'selected.mp4'; selected.write_bytes(b'new take')
    fallback = episode / 'fallback.mp4'; fallback.write_bytes(b'prior take')
    (episode / 'thin_media_report.json').write_text(json.dumps({'clips': [
        {'clip_id': 'a', 'selected': {'video': str(selected.relative_to(tmp_path))}},
        {'clip_id': 'b', 'selected': {}}]}))
    plan = {'clips': [{'clip_id': cid, 'kind': 'video'} for cid in ['a', 'b']]}
    previous = {'clips': {cid: {'video': str(fallback)} for cid in ['a', 'b']}}
    takes = store.current_takes(episode, plan, previous)
    assert takes['a']['video'] == str(selected) and takes['b']['video'] == str(fallback)
