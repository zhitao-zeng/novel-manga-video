from novel_manga.application.review.adjudication import apply
from novel_manga.repair.policy import whole_take_decision
from novel_manga.review.policy import fix_tier
from types import SimpleNamespace


def candidate():
    return {'severity': 'fail', 'identity_ok': False, 'story_ok': False, 'story_kind': '原文中有动作的人物缺席',
            'story_issue': '画面没头', 'feedback': '加头',
            'verify': {'actor_missing': True, 'count_checks': [{'entity': '甲', 'frames': '1',
                        'expected_min': 1, 'expected_max': 1, 'observed': 0, 'reason': '没露脸'}],
                       'cast_video': {'policy': 'v', 'extra_person': True}}}


def test_dismissed_crop_and_empty_suit_claims_do_not_survive_in_routing():
    answer = {'observations': ['局部腿部构图，另一套是空甲'], 'checks': [
        {'id': 0, 'evidence': '计划只拍腿，人物没有缺席', 'result': 'dismissed', 'instruction': ''}]}
    v = apply(candidate(), answer, ['疑似缺头'])
    assert v['severity'] == 'pass' and v['feedback'] == ''
    assert not v['verify'].get('actor_missing') and not v['verify'].get('count_checks')
    assert whole_take_decision(v['verify']) is None


def test_confirmed_error_routes_to_retake_and_uses_corrected_instruction():
    answer = {'checks': [{'id': 0, 'evidence': '同框确有两个甲', 'result': 'confirmed',
                          'instruction': '甲只出现一次。'}]}
    v = apply(candidate(), answer, ['疑似克隆'])
    assert v['severity'] == 'fail' and v['feedback'] == '甲只出现一次。'
    assert whole_take_decision(v['verify']).action == 'retake'
    assert fix_tier(v, SimpleNamespace(characters=[])) == 'must_fix'


def test_uncertain_evidence_does_not_approve_or_dispatch_a_retake():
    answer = {'checks': [{'id': 0, 'evidence': '该瞬间未拍到', 'result': 'uncertain', 'instruction': ''}]}
    v = apply(candidate(), answer, ['瞬间问题'])
    assert v['severity'] == 'review_error' and not v['feedback']


def test_missing_answers_cannot_clear_a_failure():
    import pytest
    with pytest.raises(ValueError):
        apply(candidate(), {'checks': []}, ['疑点'])
