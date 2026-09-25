import copy
import json
from types import SimpleNamespace

import pytest
from novel_manga.application.packing.context import context_for_plan
from novel_manga.application.packing.service import compile_plan
from novel_manga.media import inner_voice
from novel_manga.story.h3 import compose, request_issues
from novel_manga.story.voice_delivery import visual_request
from novel_manga.util import run, media_duration
from support.split_episode import split_episode


def test_packing_preserves_thought_for_postmix_but_not_for_h3(tmp_path, monkeypatch):
    episode, script, plan = split_episode.__wrapped__(tmp_path, monkeypatch)
    shot = script['shots'][0]
    shot.update(in_frame=['林凡'], camera='正面中景', motion_prompt='林凡抬眼观察')
    shot['turns'] = [{'speaker_name': '林凡', 'text': '我终于想明白了。',
                      'delivery_mode': 'offscreen_dialogue', 'inner_monologue': True}]
    (episode / 'segments.json').write_text(json.dumps([{'segment_id': 'seg_1', 'text': '林凡想明白了。'}]))
    ctx = context_for_plan(episode, episode.parent / 'story_bible.json', plan)
    ctx['profile']['inner_voice_delivery'] = 'postmix'
    result, _ = compile_plan(script, ctx)
    clip = result['clips'][0]
    assert clip['inner_voice']['text'] == '我终于想明白了。'
    assert clip['lines'][0]['inner_monologue']
    assert '我终于想明白了' not in clip['prompt']
    assert '正面中景' in clip['prompt']
    original = copy.deepcopy(clip)
    clip['prompt_h3'] = compose(clip, ['<Subject 1> looks up calmly, with naturally closed lips.'], [('', [])])
    assert '<d>' not in clip['prompt_h3'] and '<Audio' not in clip['prompt_h3']
    assert not request_issues(clip)
    assert visual_request(clip)['spoken_text'] == ''
    assert clip['lines'] == original['lines']


def test_cached_only_never_generates_a_missing_voice(tmp_path, monkeypatch):
    ctx = SimpleNamespace(work=tmp_path, novel_dir=tmp_path, cache_only=True, voice_budget=15)
    clip = {'clip_id': 'c', 'inner_voice': {'speaker': '林凡', 'text': '你好', 'voice_references': []}}
    monkeypatch.setattr(inner_voice, 'cached_recording', lambda *args: None)
    monkeypatch.setenv('NOVEL_DEMUCS_COMMAND', str(tmp_path / 'demucs'))
    (tmp_path / 'demucs').touch()
    with pytest.raises(RuntimeError, match='cache-only'):
        inner_voice.prepare(ctx, clip)


def test_mix_preserves_video_duration_and_uses_separate_audio(tmp_path, monkeypatch):
    raw = tmp_path / 'raw.mp4'; voice = tmp_path / 'voice.wav'
    run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=25:d=1',
         '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(raw)])
    run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-ar', '48000', '-ac', '2', str(voice)])
    monkeypatch.setattr(inner_voice, 'prepare', lambda *args: voice)
    output = inner_voice.mix(SimpleNamespace(), {}, raw)
    assert abs(media_duration(output) - media_duration(raw)) <= 1 / 25
    from novel_manga.media.common import audio_levels
    assert audio_levels(output)[1] > -30
    saved = output.stat().st_mtime_ns
    assert inner_voice.mix(SimpleNamespace(), {}, raw).stat().st_mtime_ns == saved
