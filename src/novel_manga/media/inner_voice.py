"""Prepare a separate thought recording, then mix it over a speech-free video take."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess

from novel_manga.util import atomic_write_json, media_duration, run
from novel_manga.runtime_backends import normalize_text
from novel_manga.media import generation
from novel_manga.media.common import reference_digests, log
from novel_manga.media import analysis
from novel_manga.story.h3 import compose


def _read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def voice_clip(clip):
    info = clip['inner_voice']; speaker = info['speaker']
    character = info.get('character_references') or [r for r in clip.get('references', [])
                                                    if r.get('role') == 'character' and r.get('name') == speaker]
    voices = list(info.get('voice_references') or [])
    if not character or not voices:
        raise RuntimeError('后期心声缺少说话人的角色卡或音色参考')
    rows = [{**row, 'stage': 1, 'delivery_mode': 'visible_dialogue', 'inner_monologue': False}
            for row in clip.get('dialogue_bindings', []) if row.get('text')]
    return {**clip, 'audio_delivery': 'native', 'references': [character[0], *voices],
            'cast': [speaker], 'dialogue_bindings': rows, 'shot_timing': [{'seconds': clip['request_seconds']}],
            'spoken_text': info['text']}


def cached_recording(ctx, clip):
    expected = normalize_text(clip['inner_voice']['text'])
    voices = [p for _, p in generation.chosen_voices(ctx, voice_clip(clip))[0]]
    for path in sorted((ctx.work / 'clips' / clip['clip_id']).glob('attempt_*/asr.json')):
        row = _read(path); request = _read(path.with_name('request.json'))
        if (row.get('passed') and normalize_text(row.get('reference', '')) == expected
                and request.get('reference_audios') == [str(p) for p in voices]
                and request.get('reference_audio_sha256') == reference_digests(voices)):
            source = path.with_name('native.wav')
            if source.is_file():
                return source
            source = path.with_name('clip.mp4')
            if source.is_file():
                return source
    return None


def isolate_voice(source, directory, *, demucs):
    raw = directory / 'source.wav'
    run(['ffmpeg', '-y', '-v', 'error', '-i', str(source), '-vn', '-ar', '44100', '-ac', '2', str(raw)])
    torch_home = Path(os.environ.get('TORCH_HOME', Path.home() / '.cache' / 'torch'))
    if not (torch_home / 'hub/checkpoints/955717e8-8726e21a.th').is_file():
        raise RuntimeError('本地缺少 htdemucs 权重；产线不自动下载模型')
    command = [str(demucs), '--two-stems', 'vocals', '-n', 'htdemucs', '-d', 'cpu',
               '-o', str(directory / 'separated'), str(raw)]
    subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                   env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TORCH_HOME': str(torch_home)})
    vocal = directory / 'separated' / 'htdemucs' / raw.stem / 'vocals.wav'
    output = directory / 'voice.wav'
    # Keep the original timing. No word cutting, speech synthesis, pitch shift or new pause.
    run(['ffmpeg', '-y', '-v', 'error', '-i', str(vocal), '-ar', '48000', '-ac', '2',
         '-c:a', 'pcm_s16le', str(output)])
    return output


def prepare(ctx, clip):
    info = clip['inner_voice']
    directory = ctx.work / 'inner_voice' / clip['clip_id']; directory.mkdir(parents=True, exist_ok=True)
    refs = [ctx.novel_dir / r['path'] for r in info.get('voice_references', [])]
    material = {'speaker': info['speaker'], 'text': info['text'],
                'voice_references': [str(p) for p in refs], 'voice_digests': reference_digests(refs),
                'source_audio': info.get('source_audio')}
    manifest = directory / 'voice.json'; output = directory / 'voice.wav'
    previous = _read(manifest)
    if output.is_file() and previous.get('input') == material:
        return output
    demucs = Path(os.environ.get('NOVEL_DEMUCS_COMMAND', '/mnt/disk1/zengzhitao/demucs_venv/bin/demucs'))
    if not demucs.is_file():
        raise RuntimeError('后期心声需要现有 Demucs 人声分离环境，尚未配置')
    source = ctx.novel_dir / info['source_audio'] if info.get('source_audio') else cached_recording(ctx, clip)
    generated = 0
    if source is None and ctx.cache_only:
        raise RuntimeError('cache-only: 缺少可复用的独立心声录音')
    if source is None:
        audio_clip = voice_clip(clip)
        for attempt in range(1, ctx.max_attempts + 1):
            take = directory / f'voice_take_{attempt:02d}'; take.mkdir(exist_ok=True)
            description = ('<Subject 1> faces the camera against a plain, uncluttered background and speaks '
                           'the bound lines quietly and evenly. The mouth follows only those lines.')
            if attempt > 1:
                description += ' The utterance starts immediately and ends cleanly, followed by silence.'
            prompt = compose(audio_clip, [description], [('', [])], soundscape='A clear solo voice over quiet room tone.')
            audio_clip['prompt_h3'] = prompt
            request, pictures, _, _ = generation.build_request(ctx, audio_clip, attempt)
            atomic_write_json(take / 'request.json', request)
            video = take / 'voice.mp4'
            generation.submit(ctx, audio_clip, request, video, pictures)
            generated += 1
            checked = analysis.analyse_clip(ctx, audio_clip, video)
            if checked['passed']:
                source = video
                break
        if source is None:
            raise RuntimeError('独立心声录音未过语音检查，停止生成画面')
    output = isolate_voice(Path(source), directory, demucs=demucs)
    atomic_write_json(manifest, {'input': material, 'source': str(source), 'generated_voice_takes': generated,
                                'audio': str(output), 'duration': media_duration(output)})
    log(f"{clip['clip_id']}: separate thought audio ready ({'reused recording' if not generated else 'new voice recording'})")
    return output


def mix(ctx, clip, video):
    voice = prepare(ctx, clip)
    directory = video.parent / 'postmix'; directory.mkdir(exist_ok=True)
    output = directory / 'clip.mp4'; manifest = directory / 'mix.json'
    inputs = {'video': str(video), 'video_stat': [video.stat().st_size, video.stat().st_mtime_ns],
              'voice': str(voice), 'voice_digest': reference_digests([voice])}
    if output.exists() and _read(manifest).get('input') == inputs:
        return output
    duration = media_duration(video)
    if media_duration(voice) > duration + .15:
        raise RuntimeError('心声录音长于画面，不能截断台词；需要调整计划时长')
    run(['ffmpeg', '-y', '-v', 'error', '-i', str(video), '-i', str(voice), '-filter_complex',
         '[0:a]volume=0.35[amb];[amb][1:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]',
         '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
         '-ar', '48000', '-ac', '2', '-t', f'{duration:.6f}', str(output)])
    for name in ['native.wav', 'asr.json', 'asr_raw.json', 'chunks.json']:
        (directory / name).unlink(missing_ok=True)
    atomic_write_json(manifest, {'input': inputs, 'duration': duration})
    return output
