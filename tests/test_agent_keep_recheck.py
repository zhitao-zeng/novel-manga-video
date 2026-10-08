import copy

import pytest

from novel_manga.application.review import execution, judges, cast_video, confirmed
from novel_manga.util import atomic_write_json, read_json


@pytest.fixture
def episode(tmp_path, monkeypatch):
    novel = tmp_path / 'book'
    directory = novel / 'book_1'
    raw = directory / 'work/clips/a/attempt_01/clip.mp4'
    mixed = raw.parent / 'postmix/clip.mp4'
    for video in (raw, mixed):
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b'existing take')
    card = novel / 'series_assets/card.jpeg'
    card.parent.mkdir(parents=True)
    card.write_bytes(b'existing reference')
    clip = {'clip_id': 'a', 'kind': 'video', 'prompt_h3': 'original English',
            'references': [{'role': 'character', 'name': '甲', 'path': 'series_assets/card.jpeg'}],
            'request_seconds': 7, 'generation_seed': 123, 'repair_take': 0,
            'lines': [{'speaker_name': '甲', 'text': '原台词', 'delivery_mode': 'visible_dialogue'}]}
    script = {'shots': [{'turns': copy.deepcopy(clip['lines']), 'actions': []}]}
    bible = dict(novel_title='书', genre='都市', visual_style='漫画', palette='自然',
                 style_fingerprint='style', characters=[])
    values = {
        'clip_plan.json': {'clips': [clip, {'clip_id': 'b', 'kind': 'video'}]},
        'chapter_script.json': script,
        'review_feedback.json': {'a': '原纠正', 'b': '其他纠正'},
        'thin_media_report.json': {'clips': [{'clip_id': 'a', 'selected': {
            'video': str(raw), 'postmix_video': str(mixed), 'passed': True, 'issues': [],
            'hypothesis': '原台词'}}]},
        'episode_review.json': {'policy': 'existing-policy', 'clips': {
            'a': {'severity': 'fail', 'tier': 'must_fix', 'verify': {'verdict': 'obvious'}},
            'b': {'severity': 'fail', 'tier': 'must_fix', 'custom': 'preserved'}},
            'feedback': {'a': '旧误报', 'b': '其他失败'},
            'flags': ['a: 旧误报', 'b: 其他失败'], 'custom': 'preserved'},
        'repair_history/history.json': {'trials': [], 'observations': {}},
    }
    atomic_write_json(novel / 'story_bible.json', bible)
    atomic_write_json(novel / 'visual_grammar.json', {'location_time': {'诊室': '白天'}})
    atomic_write_json(novel / 'profile.json', {'speech_gate': 'enforce'})
    for name, value in values.items():
        atomic_write_json(directory / name, value)
    calls = []
    monkeypatch.setattr(cast_video, 'recorded_model', lambda path: calls.append(('model', path)) or 'minimax-h3-ref2va-turbo')
    def judge(checked, video, bible, times, hypothesis, work, **kwargs):
        calls.append(('judge', copy.deepcopy(checked), video, times, hypothesis, kwargs))
        return {'severity': 'pass', 'verify': {'verdict': 'fine'}, 'feedback': ''}
    monkeypatch.setattr(judges, 'judge_clip', judge)
    observed = []
    monkeypatch.setattr(execution.history, 'observe', lambda *args: observed.append(args))
    protected = {path: path.read_bytes() for path in directory.rglob('*.json')
                 if path.name not in {'episode_review.json', 'history.json'}}
    protected[card] = card.read_bytes()
    return directory, raw, mixed, values, protected, calls, observed


def assert_frozen(protected):
    assert all(path.read_bytes() == value for path, value in protected.items())


def test_keep_pass_rechecks_selected_postmix_and_preserves_inputs_and_other_clips(episode):
    directory, raw, mixed, values, protected, calls, observed = episode
    result = execution.recheck_current_clip(directory, 'a')
    assert result['changed'] == [] and result['accepted'] == ['a']
    assert calls[0] == ('model', raw)
    _, checked, video, times, words, kwargs = calls[1]
    assert checked['_review_video_model'] == 'minimax-h3-ref2va-turbo'
    assert checked['references'] == values['clip_plan.json']['clips'][0]['references']
    assert video == mixed and times == {'诊室': '白天'} and words == '原台词'
    assert kwargs['source_script'] == values['chapter_script.json']
    review = read_json(directory / 'episode_review.json')
    assert review['clips']['a']['severity'] == 'pass'
    assert review['clips']['b'] == values['episode_review.json']['clips']['b']
    assert review['feedback'] == {'b': '其他失败'} and review['flags'] == ['b: 其他失败']
    assert review['custom'] == 'preserved'
    assert set(observed[0][2]) == {'a'}
    assert_frozen(protected)


def test_changed_mode_keep_cannot_be_reused_as_the_previous_mode(episode,monkeypatch):
    from novel_manga.application.review import episode as review_episode, evidence
    from novel_manga.review.contracts import POLICY, current_review
    directory,_,_,_,protected,_,_=episode
    previous=read_json(directory/'episode_review.json')
    previous.update(policy=POLICY,review_mode='verify')
    atomic_write_json(directory/'episode_review.json',previous)
    modes=[]
    def judge(clip,video,bible,times,hypothesis,work,**kwargs):
        modes.append(evidence.review_mode(work))
        return {'severity':'pass','verify':{'verdict':'fine'},'feedback':''}
    monkeypatch.setattr(judges,'judge_clip',judge)
    monkeypatch.setenv('NOVEL_REVIEW_MODE','classic')
    assert execution.recheck_current_clip(directory,'a')['accepted']==['a']
    saved=read_json(directory/'episode_review.json')
    assert not current_review(saved,'verify')
    assert saved['clips']['b']==previous['clips']['b']
    current=review_episode.review_episode(directory,fresh=False)
    assert modes==['classic'] and current_review(current,'classic')
    monkeypatch.setenv('NOVEL_REVIEW_MODE','verify')
    result=review_episode.review_episode(directory,fresh=False)
    assert modes==['classic','verify'] and current_review(result,'verify')
    assert_frozen(protected)


@pytest.mark.parametrize('case', ['picture', 'speech', 'extra_speech', 'confirmed'])
def test_keep_cannot_clear_picture_speech_or_human_confirmed_failure(episode, monkeypatch, case):
    directory, _, mixed, values, protected, _, _ = episode
    if case == 'picture':
        monkeypatch.setattr(judges, 'judge_clip', lambda *a, **kw: {
            'severity': 'fail', 'verify': {'verdict': 'obvious'}, 'feedback': '保留正确服装'})
    elif case in {'speech', 'extra_speech'}:
        media = read_json(directory / 'thin_media_report.json')
        issue = 'excess_unplanned_speech' if case == 'extra_speech' else 'missing_speech'
        media['clips'][0]['selected'].update(passed=False, issues=[issue])
        atomic_write_json(directory / 'thin_media_report.json', media)
        protected[directory / 'thin_media_report.json'] = (directory / 'thin_media_report.json').read_bytes()
    else:
        previous = read_json(directory / 'episode_review.json')
        take = execution.current_takes(directory, values['clip_plan.json'], previous)['a']
        previous['clips']['a']['confirmed'] = {**take, 'issue': '人工确认多出身体', 'instruction': '同框一个身体'}
        atomic_write_json(directory / 'episode_review.json', previous)
        monkeypatch.setattr(confirmed, 'judge_finding', lambda *a, **kw: pytest.fail('same human-confirmed take must not be cleared'))
    result = execution.recheck_current_clip(directory, 'a')
    assert result['accepted'] == [] and result['changed'] == [] and result['skip_render']
    assert result['review']['clips']['a']['severity'] == 'fail'
    assert 'a' in result['review']['feedback']
    assert result['review']['clips']['b'] == values['episode_review.json']['clips']['b']
    assert_frozen(protected)


@pytest.mark.parametrize('case', ['exception', 'review_error', 'take_changed'])
def test_incomplete_or_changed_take_keeps_previous_failure(episode, monkeypatch, case):
    directory, _, mixed, _, protected, _, observed = episode
    before = (directory / 'episode_review.json').read_bytes()
    def judge(*args, **kwargs):
        if case == 'exception':
            raise RuntimeError('review failed')
        if case == 'take_changed':
            mixed.write_bytes(b'a replacement video')
        return {'severity': 'review_error' if case == 'review_error' else 'pass'}
    monkeypatch.setattr(judges, 'judge_clip', judge)
    with pytest.raises(ValueError):
        execution.recheck_current_clip(directory, 'a')
    assert (directory / 'episode_review.json').read_bytes() == before
    assert not observed
    assert_frozen(protected)
