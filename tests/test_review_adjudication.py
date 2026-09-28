from novel_manga.application.review.adjudication import apply, ERROR_FACTS
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
                          'error_facts': {k: k == 'same_person_twice' for k in ERROR_FACTS}, 'request_conflict': False,
                          'instruction': '甲只出现一次。'}]}
    original = candidate()
    original['verify']['actor_missing'] = False
    v = apply(original, answer, ['疑似克隆'])
    assert v['severity'] == 'fail' and v['feedback'] == '甲只出现一次。'
    assert whole_take_decision(v['verify']).action == 'retake'
    assert fix_tier(v, SimpleNamespace(characters=[])) == 'must_fix'
    assert v['verify']['evidence'] == '同框确有两个甲'
    assert v['verify']['error_kinds'] == ['same_person_twice']


def test_confirmed_categories_and_evidence_reach_retry_history(tmp_path):
    from novel_manga.application.repair import history
    take = {'video': 'v.mp4', 'take': [1, 2, 3]}
    verdict = {**take, 'verify': {'verdict': 'obvious', 'evidence': '确有多余空甲',
                                  'error_kinds': ['extra_object'], 'instruction': '只保留计划内的装甲。'}}
    record = {'observations': {}}
    history.add_observations(record, {'clips': {'c': verdict}}, {'c': take})
    second = {**take, 'video': 'v2.mp4'}
    history.add_observations(record, {'clips': {'c': {**verdict, **second}}}, {'c': second})
    history.save(tmp_path, record)
    assert history.repeated_errors(tmp_path, 'c') == ['extra_object']
    assert record['observations']['c'][0]['evidence'] == '确有多余空甲'


def test_uncertain_evidence_does_not_approve_or_dispatch_a_retake():
    answer = {'checks': [{'id': 0, 'evidence': '该瞬间未拍到', 'result': 'uncertain', 'instruction': ''}]}
    v = apply(candidate(), answer, ['瞬间问题'])
    assert v['severity'] == 'review_error' and not v['feedback']


def test_missing_answers_cannot_clear_a_failure():
    import pytest
    with pytest.raises(ValueError):
        apply(candidate(), {'checks': []}, ['疑点'])


def test_confirming_an_attribution_concern_does_not_guess_its_cause():
    answer = {'checks': [{'id': 0, 'evidence': '甲的动作确实交给了乙', 'result': 'confirmed',
                          'error_facts': {k: k == 'action_by_wrong_person' for k in ERROR_FACTS}, 'request_conflict': False, 'instruction': '甲完成交接。'}]}
    v = apply(candidate(), answer, ['动作归属疑点'])
    assert v['severity'] == 'fail'
    assert whole_take_decision(v['verify']) is None  # current script/request/frames go to the existing diagnosis


def test_wrong_clothes_cannot_keep_a_stale_clone_category_or_count_signal():
    answer = {'checks': [{'id': 0, 'evidence': '两人各一个身体，席勒穿了西装和领带', 'result': 'confirmed',
                         'kinds': ['same_person_twice'],  # stale caller label is not evidence
                         'error_facts': {k: k == 'appearance' for k in ERROR_FACTS}, 'request_conflict': False,
                         'instruction': '席勒穿白衬衫和灰马甲'}]}
    v = apply(candidate(), answer, ['衣着错误'])
    assert v['verify']['error_kinds'] == ['appearance']
    assert whole_take_decision(v['verify']).action == 'retake'


def test_request_costume_conflict_goes_to_rewrite_without_blind_retake(tmp_path, monkeypatch):
    from novel_manga.application.review import adjudication
    from novel_manga.application.preparation import request_check
    answer = {'checks': [{'id': 0, 'evidence': '请求写深色便装，实际参考为白衬衫马甲，画面照请求换了衣服',
                         'result': 'confirmed', 'error_facts': {k: k == 'appearance' for k in ERROR_FACTS},
                         'request_conflict': True, 'instruction': '请求与参考统一为白衬衫马甲'}]}
    v = apply(candidate(), answer, ['衣着错误'])
    monkeypatch.setattr(request_check, 'current_request', lambda *a: {'consistent': False,
        'observations': ['图1白衬衫马甲'], 'problems': ['请求写深色便装']})
    v = adjudication.check_inputs({'prompt_h3': 'dark clothes', 'references': [{'role': 'character'}]},
                                 tmp_path / 'book/ep/work/review/c', v)
    route = whole_take_decision(v['verify'])
    assert route.action == 'reframe' and route.diagnosis['cause'] == 'request_mismatch'


def test_missing_classification_is_not_guessed_as_a_clone():
    import pytest
    with pytest.raises(ValueError, match='explicit observed error facts'):
        apply(candidate(), {'checks': [{'id': 0, 'result': 'confirmed', 'evidence': '衣着错',
                                       'kinds': ['same_person_twice'], 'instruction': '改衣服'}]}, ['衣着错'])


def test_confirmed_extra_person_uses_retake_only_after_current_input_was_checked():
    answer = {'checks': [{'id': 0, 'evidence': '两人计划中出现第三个人', 'result': 'confirmed',
                         'error_facts': {k: k == 'extra_person' for k in ERROR_FACTS},
                         'instruction': '只出现原来的两个人'}]}
    v = apply(candidate(), answer, ['多余人物'])
    assert whole_take_decision(v['verify']) is None
    v['verify']['request_check'] = {'consistent': True, 'problems': []}
    assert whole_take_decision(v['verify']).action == 'retake'


def test_a_frame_dismissal_cannot_approve_a_known_request_reference_conflict(tmp_path, monkeypatch):
    from novel_manga.application.review import adjudication
    from novel_manga.application.preparation import request_check
    monkeypatch.setattr(request_check, 'current_request', lambda *a: {'consistent': False,
        'observations': ['图1是白衬衫与灰马甲'], 'problems': ['请求写深色便装，无换装情节']})
    verdict = apply(candidate(), {'checks': [{'id': 0, 'result': 'dismissed',
                      'evidence': '画面符合请求的深色便装', 'instruction': ''}]}, ['衣服错误'])
    clip = {'prompt_h3': 'dark clothes', 'references': [{'role': 'character', 'path': 'doctor.jpeg'}]}
    final = adjudication.check_inputs(clip, tmp_path / 'book/ep/work/review/c', verdict)
    assert verdict['severity'] == 'pass' and final['severity'] == 'fail'
    assert final['verify']['request_conflict'] is True and final['verify']['error_kinds'] == []
    assert whole_take_decision(final['verify']).action == 'reframe'


def test_old_classification_is_refreshed_once_without_rewatching_unchanged_take(tmp_path, monkeypatch):
    import json
    from novel_manga.application.review import episode, adjudication, cast_video, judges
    from novel_manga.models.bible import StoryBible
    from novel_manga.review import contracts, storage
    book = tmp_path / 'book'; directory = book / 'book_1'; directory.mkdir(parents=True)
    (book / 'story_bible.json').write_text(StoryBible(novel_title='测试', genre='g', visual_style='v', palette='p',
        style_fingerprint='f', characters=[], locations=[]).model_dump_json())
    video = directory / 'v.mp4'; video.write_bytes(b'unchanged video')
    (directory / 'clip_plan.json').write_text(json.dumps({'clips': [{'clip_id': 'c', 'kind': 'video'}]}))
    (directory / 'thin_media_report.json').write_text(json.dumps({'clips': [{'clip_id': 'c', 'selected': {'video': str(video)}}]}))
    old = {'policy': contracts.POLICY, 'clips': {'c': {'video': str(video), 'take': storage.take_identity(video),
        'severity': 'fail', 'identity_issue': '衣着错误', 'scripted': False,
        'verify': {'cast_video': {'policy': cast_video.POLICY}, 'error_kinds': ['same_person_twice'],
                   'adjudication': {'confirmed': True}}}}, 'feedback': {'c': '改衣服'}}
    (directory / 'episode_review.json').write_text(json.dumps(old))
    calls = []
    def classify(clip, video, bible, work, verdict):
        calls.append(1)
        return apply(verdict, {'checks': [{'id': 0, 'result': 'confirmed', 'evidence': '同一人穿错衣服',
                      'error_facts': {k: k == 'appearance' for k in ERROR_FACTS}, 'request_conflict': False,
                      'instruction': '穿白衬衫和灰马甲'}]}, ['衣着错误'])
    monkeypatch.setenv('NOVEL_REVIEW_CAST_VIDEO', '1')
    monkeypatch.setattr(adjudication, 'review', classify)
    monkeypatch.setattr(judges, 'judge_clip', lambda *a, **k: (_ for _ in ()).throw(AssertionError('do not rewatch')))
    result = episode.review_episode(directory)
    assert result['clips']['c']['verify']['error_kinds'] == ['appearance']
    assert episode.review_episode(directory) == result and calls == [1]
