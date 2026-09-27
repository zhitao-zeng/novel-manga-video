import copy
import pytest
from novel_manga.application.repair.source_recheck import reuse_allowed, SourceVerifier
from novel_manga.review.policy import verify_to_verdict
from novel_manga.review.reconciliation import verdict_from_record


def test_source_fine_with_unknown_generic_story_field_can_keep_visually_passed_footage():
    answer = {'verdict': 'fine', 'people': [], 'count_checks': []}
    assert verify_to_verdict(answer)['story_ok'] is None
    assert reuse_allowed(answer, {'severity': 'pass'}, True)
    assert not reuse_allowed(answer, {'severity': 'fail'}, True)
    assert not reuse_allowed(answer, {'severity': 'review_error'}, True)
    assert not reuse_allowed(answer, {'severity': 'pass'}, False)
    assert not reuse_allowed({}, {'severity': 'pass'}, True)


def test_source_and_picture_failures_are_both_preserved():
    assert not reuse_allowed({'verdict': 'fine', 'actor_missing': True}, {'severity': 'pass'}, True)
    assert reuse_allowed({'verdict': 'subtle'}, {'severity': 'minor'}, True)


def test_picture_failure_is_not_lost_when_source_confirmation_is_imported():
    picture = {'severity': 'fail', 'identity_ok': False, 'identity_issue': '画面确有多余装甲',
               'feedback': '只保留计划内的一套装甲。', 'source_segment_ids': ['s1'],
               'verify': {'verdict': 'obvious', 'cast_video': {'policy': 'v3'}, 'error_kinds': ['extra_object']}}
    record = {'video': 'v.mp4', 'take': [1, 2, 3], 'mode': 'source_confirm', 'source_only_verdict': 'fine',
              'verdict': 'obvious', 'people': [], 'picture_review': picture, 'source_confirmed_at': 'now'}
    before = copy.deepcopy(record)
    result = verdict_from_record(record)
    assert result['severity'] == 'fail' and result['tier'] == 'must_fix'
    assert result['verify'] == picture['verify'] and result['source_segment_ids'] == ['s1']
    assert record == before


def test_a_source_verifier_cannot_approve_with_no_source_addresses(tmp_path):
    verifier = object.__new__(SourceVerifier)
    with pytest.raises(ValueError, match='source addresses'):
        verifier.prompt_for({'segment_ids': []}, tmp_path, 1, '')
