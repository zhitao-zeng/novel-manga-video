import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from novel_manga.media import analysis, speech


@pytest.mark.parametrize('reference,heard,level,issues', [
    ('', '', -99, []),
    ('', '我来给你讲故事', -20, ['unscripted_speech']),
    ('', '我来给你讲故事', -90, []),
    ('欢迎光临', '欢迎光临', -20, []),
])
def test_confirmed_wordless_detection_keeps_asr_calls_and_thresholds(tmp_path, monkeypatch, reference, heard, level, issues):
    video = tmp_path / 'clip.mp4'; video.write_bytes(b'video')
    (tmp_path / 'native.wav').write_bytes(b'audio')
    ctx = SimpleNamespace(asr_python='test-python', asr_helper='test-asr', protected_terms=[], aliases={})
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
