from novel_manga.media.context import RenderContext, RenderOptions, RenderState
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from novel_manga.media import analysis, speech
from novel_manga.media.evidence_backends import correct_protected_lexicon


def test_resumed_clause_after_omission_is_not_classified_as_new_speech():
    from novel_manga.media.subtitles import align_chunks
    reference='这些人都来过这里。甲和乙之前分别做过很多不同的事情。但是，他们居然拿走了我的法杖去换晚餐！'
    rows=[{'start':0,'end':2,'hypothesis':'这些人都来过这里'},
          {'start':3,'end':5,'hypothesis':'但他们居然拿走了我的法杖去换晚餐'}]
    result=speech.evaluate(reference,rows,-20,-3)
    assert 'excess_unplanned_speech'not in result['issues']
    assert result['missing']>0  # The skipped dialogue is still measured.
    matched=align_chunks([reference],rows)
    assert matched[-1][1]and'法杖'in matched[-1][1]
    assert matched[-1][2]>=.65


@pytest.mark.parametrize('utterance',['请点赞并订阅我们的视频频道','这些人都来过这里'])
def test_clause_rejoin_cannot_hide_adlib_or_repeat(utterance):
    reference='这些人都来过这里。甲和乙之前分别做过很多不同的事情。但是，他们拿走了法杖。'
    rows=[{'start':0,'end':2,'hypothesis':'这些人都来过这里'},
          {'start':3,'end':5,'hypothesis':utterance}]
    assert 'excess_unplanned_speech'in speech.evaluate(reference,rows,-20,-3)['issues']


@pytest.mark.parametrize('reference,heard,level,issues', [
    ('', '', -99, []),
    ('', '我来给你讲故事', -20, ['unscripted_speech']),
    ('', '我来给你讲故事', -90, []),
    ('欢迎光临', '欢迎光临', -20, []),
])
def test_confirmed_wordless_detection_keeps_asr_calls_and_thresholds(tmp_path, monkeypatch, reference, heard, level, issues):
    video = tmp_path / 'clip.mp4'; video.write_bytes(b'video')
    (tmp_path / 'native.wav').write_bytes(b'audio')
    ctx = RenderContext(asr_python='test-python', asr_helper='test-asr', protected_terms=[], aliases={})
    monkeypatch.setattr(analysis, 'media_duration', lambda *a: 10)
    monkeypatch.setattr(analysis, 'audio_levels', lambda *a: (level, level))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[0] == 'ffmpeg':
            return SimpleNamespace(stderr='silence_start: 0\nsilence_end: 10' if level < -40 else '')
        Path(command[command.index('--output') + 1]).write_text(json.dumps({
            'segments': [{'start': 0, 'end': 10, 'hypothesis': heard}]}))
        return SimpleNamespace()
    monkeypatch.setattr(analysis.subprocess, 'run', run)
    result = analysis.analyse_clip(ctx, {'clip_id': 'c', 'spoken_text': reference}, video)
    assert result['issues'] == issues and result['hypothesis'] == heard
    assert [command[0] for command in calls] == ['ffmpeg', 'test-python']
    assert analysis.analyse_clip(ctx, {'clip_id': 'c', 'spoken_text': reference}, video) == result
    assert len(calls) == 2


def test_changed_dialogue_reuses_recognition_and_updates_only_current_evaluation(tmp_path, monkeypatch):
    video = tmp_path / 'clip.mp4'; video.write_bytes(b'video')
    (tmp_path / 'native.wav').write_bytes(b'audio')
    ctx = SimpleNamespace(protected_terms=[], aliases={})
    rows = [{'start': 1, 'end': 3, 'hypothesis': '请进。', 'raw_hypothesis': '请进。', 'corrections': []}]
    old = {'clip_id': 'c', 'video': str(video), 'duration': 10, **speech.evaluate('开门。', rows, -20, -10)}
    assert not old['passed']
    (tmp_path / 'asr.json').write_text(json.dumps(old))
    monkeypatch.setattr(analysis, 'media_duration', lambda *a: 10)
    monkeypatch.setattr(analysis.subprocess, 'run', lambda *a, **k: pytest.fail('must reuse recognition'))
    updated = analysis.analyse_clip(ctx, {'clip_id': 'c', 'spoken_text': '请进。'}, video)
    assert updated['passed'] and updated['reference'] == '请进。'
    assert updated['chunks'] == rows and updated['missing'] == 0
    assert json.loads((tmp_path / 'asr.json').read_text()) == updated
    before = copy.deepcopy(updated)
    assert speech.recheck('请进。', updated, [], {})['passed']
    assert updated == before


def test_proper_name_spelling_correction_keeps_raw_recognition_and_exact_changes():
    rows = [{'hypothesis': '科茶来了。', 'start': 0, 'end': 2}]
    result = speech.corrected_rows(rows, '科查来了。', ['科查'], {})[0]
    assert result['raw_hypothesis'] == '科茶来了。'
    assert result['hypothesis'] == '科查来了。'
    assert result['corrections'][0]['source'] == '科茶'
    assert result['corrections'][0]['canonical'] == '科查'


def test_spelling_correction_does_not_replace_a_different_registered_name_or_fill_missing_name():
    for heard, reference, names in [('林奇来了', '林齐来了', ['林齐', '林奇']),
                                   ('恩来了', '恩佐来了', ['恩佐'])]:
        corrected, changes = correct_protected_lexicon(heard, reference, names)
        assert corrected == heard and changes == []


def test_planned_laughter_keeps_raw_asr_but_does_not_count_as_extra_words():
    clip = {'shot_sound': ['大笑声'], 'shot_timing': [{'seconds': 5}]}
    events = speech.planned_vocalizations(clip)
    rows = [{'start': 0, 'end': 1, 'hypothesis': '请进'},
            {'start': 2, 'end': 4, 'hypothesis': '哈哈哈哈哈哈'}]
    saved = copy.deepcopy(rows)
    assert speech.evaluate('请进', rows, -20, -10)['warnings'] == ['nonverbal_laughter']
    result = speech.evaluate('请进', rows, -20, -10, vocalizations=events)
    assert result['passed'] and result['nonverbal_chunks'] == [rows[1]]
    assert result['chunks'] == saved and rows == saved
    assert result['hypothesis'] == '请进哈哈哈哈哈哈' and result['speech_hypothesis'] == '请进'
    assert speech.recheck('请进', result, [], {}, vocalizations=events)['passed']
    assert not speech.evaluate('小心危险赶快躲开', rows, -20, -10, vocalizations=events)['passed']
    assert not speech.evaluate('请进', [rows[0], {**rows[1], 'hypothesis': '哈哈今天我来给你讲故事'}],
                               -20, -10, vocalizations=events)['passed']
    assert speech.evaluate('请进', [rows[0], {**rows[1], 'start': 7, 'end': 9}],
                           -20, -10, vocalizations=events)['warnings'] == ['nonverbal_laughter']
    assert speech.planned_vocalizations({'shot_sound': ['微笑'], 'shot_timing': [{'seconds': 5}]}) == []


def test_wordless_planned_laughter_does_not_hide_unplanned_words():
    events = [{'kind': 'laughter', 'start': 0, 'end': 4}]
    rows = [{'start': 0, 'end': 3, 'hypothesis': '呵呵呵呵呵呵'}]
    assert speech.evaluate('', rows, -20, -10, vocalizations=events)['passed']
    rows[0]['hypothesis'] = '呵呵请你坐下'
    assert speech.evaluate('', rows, -20, -10, vocalizations=events)['issues'] == ['unscripted_speech']
