"""Candidate-only export regression; no decoder, model, container or production writes."""
import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.review.storage import take_identity


CANDIDATE = Path(__file__).resolve().parents[1]


@pytest.fixture
def agent(monkeypatch):
    path = CANDIDATE/'src/novel_manga/application/repair/agent.py'
    spec = importlib.util.spec_from_file_location('agent_frame_evidence_candidate', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'project_root', lambda: CANDIDATE)
    return module


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def setup_episode(tmp_path):
    directory = tmp_path/'book/book_1'
    current = directory/'work/clips/clip_09/attempt_01/clip.mp4'
    historical = directory/'repair_history/takes/clip_09/old/clip.mp4'
    for index, video in enumerate((current, historical)):
        video.parent.mkdir(parents=True)
        video.write_bytes(f'fake take {index}'.encode())
        write(video.parent/'request.json', {'prompt': f'request {index}'})
        write(video.parent/'asr.json', {'hypothesis': f'ASR {index}'})
    clip = {'clip_id': 'clip_09', 'repair_take': 2, 'references': [], 'shot_indexes': [1],
            'prompt_h3': '\n\n'.join(f'{name}:\nNone.' for name in (
                'subject_definitions', 'summary', 'retention_analysis', 'detailed_description',
                'overall_soundscape', 'non_diegetic_music'))}
    media = {'clips': [{'clip_id': 'clip_09', 'selected': {'video': str(current)}}]}
    write(directory/'thin_media_report.json', media)
    write(directory/'chapter_script.json', {'shots': [{'index': 1, 'event': 'original event'}]})
    frames = directory/'work/review/clip_09'
    frames.mkdir(parents=True)
    for i in range(1, 6):
        (frames/f'frame_{i}.jpg').write_bytes(f'existing frame {i}'.encode())
    write(directory/'episode_review.json', {'clips': {'clip_09': {
        'video': str(current), 'take': take_identity(current), 'severity': 'fail'}}})
    write(frames/'frame-evidence.json', {'video': str(current), 'take': take_identity(current),
        'frames': [{'path': str(frames/f'frame_{i}.jpg'), 'seconds': i} for i in range(1, 4)]})
    return directory, current, historical, clip, media


def mock_host_frames(agent, monkeypatch):
    calls = []
    def extract(video, target, count):
        calls.append((video, target, count))
        target.mkdir(parents=True, exist_ok=True)
        frames = []
        for i in range(1, count + 1):
            frame = target/f'frame_{i}.jpg'
            frame.write_bytes(f'extracted frame {i}'.encode())
            frames.append(frame)
        return frames
    monkeypatch.setattr(agent, 'clip_frames', extract)
    return calls




def test_three_frame_review_does_not_reuse_a_stale_fourth_frame_with_plausible_mtime(tmp_path, monkeypatch, agent):
    directory, current, _, _, _ = setup_episode(tmp_path)
    stale = directory/'work/review/clip_09/frame_4.jpg'
    stale.write_bytes(b'frame from a different earlier review')
    write(directory/'episode_review.json', {'clips': {'clip_09': {
        'video': str(current), 'take': take_identity(current), 'severity': 'fail'}}})
    assert current.stat().st_mtime_ns <= stale.stat().st_mtime_ns <= (directory/'episode_review.json').stat().st_mtime_ns
    monkeypatch.setattr(agent, 'clip_frames', lambda *a: pytest.fail('valid three-frame review should be reused'))
    frames, provenance = agent.provided_frames(directory, 'clip_09', current, tmp_path/'run/input/take-1/frames')
    assert provenance == 'same_take_review_report_supported'
    assert [p.name for p in frames] == ['frame_1.jpg', 'frame_2.jpg', 'frame_3.jpg']


def test_same_review_exports_the_late_fourth_action_frame(tmp_path, monkeypatch, agent):
    directory, current, _, _, _ = setup_episode(tmp_path)
    cache = directory/'work/review/clip_09'
    write(cache/'frame-evidence.json', {'video': str(current), 'take': take_identity(current),
        'frames': [{'path': str(cache/f'frame_{i}.jpg'), 'seconds': i * 3} for i in range(1, 5)]})
    write(directory/'episode_review.json', {'clips': {'clip_09': {
        'video': str(current), 'take': take_identity(current), 'severity': 'fail'}}})
    monkeypatch.setattr(agent, 'clip_frames', lambda *a: pytest.fail('use all four supported frames'))
    target = tmp_path/'run/input/frames'
    frames, provenance = agent.provided_frames(directory, 'clip_09', current, target)
    assert provenance == 'same_take_review_report_supported'
    assert [p.name for p in frames] == [f'frame_{i}.jpg' for i in range(1, 5)]
    assert json.loads((target/'frame-evidence.json').read_text())['frames'][-1]['seconds'] == 12


def test_legacy_review_without_frame_list_extracts_tail_instead_of_guessing(tmp_path, monkeypatch, agent):
    directory, current, _, _, _ = setup_episode(tmp_path)
    (directory/'work/review/clip_09/frame-evidence.json').unlink()
    calls = mock_host_frames(agent, monkeypatch)
    frames, provenance = agent.provided_frames(directory, 'clip_09', current, tmp_path/'input/frames')
    assert provenance == 'host_uniform_10_to_90_percent'
    assert len(frames) == 4 and len(calls) == 1


@pytest.mark.parametrize('problem', ['different_take', 'missing_frame', 'old_frame', 'postmix_video', 'review_error'])
def test_unconfirmed_review_frames_are_extracted_only_inside_run_copy(tmp_path, monkeypatch, agent, problem):
    directory, current, _, _, _ = setup_episode(tmp_path)
    review_path = directory/'episode_review.json'
    review = json.loads(review_path.read_text())
    row = review['clips']['clip_09']
    cache = directory/'work/review/clip_09'
    if problem == 'different_take':
        row['take'][0] += 1
    elif problem == 'missing_frame':
        (cache/'frame_3.jpg').unlink()
    elif problem == 'old_frame':
        old = current.stat().st_mtime_ns - 1_000_000
        os.utime(cache/'frame_1.jpg', ns=(old, old))
    elif problem == 'postmix_video':
        row['video'] = str(current.parent/'postmix/clip.mp4')
    else:
        row['severity'] = 'review_error'
    write(review_path, review)
    cached_before = {p: p.read_bytes() for p in cache.iterdir()}
    calls = mock_host_frames(agent, monkeypatch)
    target = tmp_path/'sandbox-runs/run/input/take-1/frames'
    frames, provenance = agent.provided_frames(directory, 'clip_09', current, target)
    assert provenance == 'host_uniform_10_to_90_percent'
    assert calls == [(current, target, 4)] and len(frames) == 4
    assert {p: p.read_bytes() for p in cache.iterdir()} == cached_before
