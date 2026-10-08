from novel_manga.media.context import RenderContext, RenderOptions, RenderState
"""The same affirmative-direction contract applies at preparation and actual H3 submission."""
import copy
import json
import wave
from types import SimpleNamespace

import pytest

from novel_manga.application.preparation import actions, readiness
from novel_manga.application.profiles import h3_stamp
from novel_manga.application.rendering import h3
from novel_manga.media import generation, speech_repair, inner_voice
from novel_manga.story.h3 import request_issues
from support.generation import runner, saved


POSITIVE = (
    'subject_definitions:\n<Subject 1> identifies the person in <Picture 1>.\n\n'
    'summary:\n[reference generation] A four-second animated close-up.\n\n'
    'retention_analysis:\n<Subject 1>: fully_preserved - identity and outfit.\n\n'
    'detailed_description:\n[Shot 1] <Subject 1> faces the listener. '
    '<Subject 1> (S1) says <d>[Chinese] 不然呢?</d> His lips close.\n\n'
    'overall_soundscape:\nNone.\n\nnon_diegetic_music:\nNone.'
)


def current_clip():
    clip = {'clip_id': 'c', 'kind': 'video', 'prompt': '【阶段一】人物提问。',
            'prompt_h3': POSITIVE, 'request_seconds': 4,
            'references': [{'role': 'character', 'name': '甲', 'path': 'card.jpeg'}],
            'dialogue_bindings': [{'stage': 1, 'speaker_name': '甲',
                                   'delivery_mode': 'visible_dialogue', 'text': '不然呢？'}]}
    clip['prompt_h3_of'] = h3_stamp(clip)
    return clip






@pytest.mark.parametrize('actual', [POSITIVE.replace('不然呢?', '另一句台词。'),
                                   POSITIVE + '\n【合规】没有背景音乐。'])
def test_final_h3_submission_checks_actual_request_and_preserves_input(tmp_path, actual):
    clip = current_clip(); before = copy.deepcopy(clip); calls = []
    context = SimpleNamespace(settings=SimpleNamespace(local_h3_base_url='pool'),
                              provider=SimpleNamespace(create_video=lambda *a, **k: calls.append(k)))
    request = {'prompt': actual, 'reference_audios': [], 'seed_variant': 0}
    with pytest.raises(ValueError, match='H3 final request invalid'):
        generation.submit(context, clip, request, tmp_path/'clip.mp4', ())
    assert calls == [] and clip == before


def test_final_h3_submission_keeps_negative_dialogue_and_none_soundscape(tmp_path):
    clip = current_clip(); calls = []
    context = SimpleNamespace(settings=SimpleNamespace(local_h3_base_url='pool'),
                              provider=SimpleNamespace(create_video=lambda *a, **k: calls.append((a, k))))
    assert request_issues(clip) == []
    generation.submit(context, clip, {'prompt': POSITIVE, 'reference_audios': [], 'seed_variant': 0},
                      tmp_path/'clip.mp4', ())
    assert calls[0][0][0] == POSITIVE


def test_legacy_negative_direction_reaches_provider_unchanged(tmp_path):
    clip = current_clip(); calls = []
    actual = POSITIVE + '\nNo other people appear.'
    context = SimpleNamespace(settings=SimpleNamespace(local_h3_base_url='pool'),
                              provider=SimpleNamespace(create_video=lambda *a, **k: calls.append(a[0])))
    generation.submit(context, clip, {'prompt': actual, 'reference_audios': [], 'seed_variant': 0},
                      tmp_path/'clip.mp4', ())
    assert calls == [actual]


def test_final_check_does_not_change_other_video_suppliers(tmp_path):
    calls = []
    context = SimpleNamespace(settings=SimpleNamespace(local_h3_base_url=None),
                              provider=SimpleNamespace(create_video=lambda *a, **k: calls.append(a[0])))
    generation.submit(context, {'request_seconds': 4},
                      {'prompt': '不要生成字幕。', 'reference_audios': []}, tmp_path/'sd.mp4', ())
    assert calls == ['不要生成字幕。']


def test_legacy_cached_take_returns_before_the_new_submission_guard(tmp_path, monkeypatch):
    run = runner(tmp_path, local='pool', cache_only=True)
    clip = {'clip_id': 'clip_01', 'kind': 'video', 'prompt': '中文',
            'prompt_h3': 'No extra objects appear.', 'request_seconds': 10, 'references': [], 'lines': []}
    take = run.context.work/'clips'/'clip_01'/'attempt_01'; take.mkdir(parents=True)
    (take/'clip.mp4').write_bytes(b'existing accepted media')
    (take/'request.json').write_text(json.dumps(saved(clip['prompt_h3'])))
    monkeypatch.setattr(generation, 'submit', lambda *a, **k: pytest.fail('a matching cache must not submit'))
    assert run.generate_clip(clip, 1) == take/'clip.mp4'




def test_separate_thought_recording_also_stops_before_provider_for_invalid_final_prompt(tmp_path, monkeypatch):
    clip = current_clip(); (tmp_path/'card.jpeg').write_bytes(b'cached character card')
    with wave.open(str(tmp_path/'voice.wav'), 'wb') as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000)
        handle.writeframes(b'\0\0' * 1600)
    clip['inner_voice'] = {'speaker': '甲', 'text': '不然呢？',
                           'character_references': clip['references'],
                           'voice_references': [{'role': 'voice', 'name': '甲', 'path': 'voice.wav'}]}
    demucs = tmp_path/'demucs'; demucs.touch()
    monkeypatch.setenv('NOVEL_DEMUCS_COMMAND', str(demucs))
    monkeypatch.setattr(inner_voice, 'cached_recording', lambda *a: None)
    monkeypatch.setattr(inner_voice, 'compose', lambda *a, **k: POSITIVE + '\n额外制作说明。')
    context = RenderContext(novel_dir=tmp_path, work=tmp_path / 'work', feedback={}, settings=SimpleNamespace(local_h3_base_url='pool'), provider=SimpleNamespace(create_video=lambda *a, **k: pytest.fail('invalid recording submitted')), options=RenderOptions(cache_only=False, max_attempts=1, voice_budget=29))
    with pytest.raises(ValueError, match='Chinese outside bound dialogue'):
        inner_voice.prepare(context, clip)
