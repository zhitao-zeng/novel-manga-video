import novel_manga.application.rendering.submission as owner_submission
from novel_manga.application.rendering import submission as render_submission
import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from support.render_context import uninitialized_runner
from novel_manga.application.profiles import plan_fingerprint
from novel_manga.application.repair import history, managed
from novel_manga.application.rendering import flow, reviewed
from novel_manga.media import generation
from novel_manga.util import read_json


def add_trial(directory, plan):
    state = history.load(directory)
    state['trials'].append({'id': len(state['trials']) + 1, 'managed': True, 'clips': ['c'],
                            'expected_plan': plan_fingerprint(plan), 'expected_notes': {}, 'renders': []})
    history.save(directory, state)
    return state['trials'][-1]['id']


@pytest.fixture
def episode(tmp_path, monkeypatch):
    directory = tmp_path / 'book' / 'book_1'
    directory.mkdir(parents=True)
    clip = {'clip_id': 'c', 'kind': 'video', 'prompt': 'The actor stands beside the table.',
            'request_seconds': 5, 'references': [], 'spoken_text': '好吧。'}
    plan = {'clips': [clip]}
    (directory / 'clip_plan.json').write_text(json.dumps(plan))
    (directory / 'chapter_script.json').write_text('{"shots": []}')
    add_trial(directory, plan)
    monkeypatch.setattr(render_submission, 'acquire_inflight_slot', lambda *a: object())
    monkeypatch.setattr(render_submission, 'release_inflight_slot', lambda *a: None)
    monkeypatch.setattr(owner_submission, 'wait_for_inflight_redraws', lambda *a: None)
    monkeypatch.setattr(render_submission, 'media_duration', lambda *a: 5.0)
    monkeypatch.setattr(flow, 'record_privacy_ok', lambda *a: None)
    from novel_manga.application.preparation import readiness
    monkeypatch.setattr(readiness, 'plan_issues', lambda *a: {})
    monkeypatch.setattr(readiness, 'reference_issues', lambda *a: [])
    monkeypatch.setattr(readiness, 'render_risk_report', lambda *a: {})
    monkeypatch.setattr(readiness, 'save_check', lambda *a: None)
    return directory, clip, plan


def runner(directory, clip, provider):
    r = uninitialized_runner()
    r.context.episode_dir = directory
    r.context.novel_dir = directory.parent
    r.context.work = directory / 'work'
    r.context.settings = SimpleNamespace(local_h3_base_url=None)
    r.context.clip_plan = {'clips': [copy.deepcopy(clip)]}
    r.context.script = {'shots': []}
    r.context.provider = SimpleNamespace(create_video=provider)
    r.context.state.remaining = {'c': max(0, 3 - history.generation_counts(directory).get('c', 0))}
    r.build_assets = lambda *a, **kw: None
    r.english_correction_for_new_take = lambda *a: False
    r.assemble = lambda *a: {'final_video': str(directory / 'candidate.mp4')}
    r.analyse_clip = lambda c, video: {'video': str(video), 'duration': 5.0, 'cer': 0.0,
                                      'max_volume_db': -10.0, 'issues': [], 'passed': True}
    return r


def task(output, task_id):
    output.with_suffix(output.suffix + '.task.json').write_text(json.dumps({'task_id': task_id}))


def completed_provider(calls, task_id='task-1'):
    def provider(prompt, image, output, **kwargs):
        calls.append(task_id)
        task(output, task_id)
        output.write_bytes(b'generated video')
        return output
    return provider


def assert_report_then_history(monkeypatch, directory):
    original = history.record_render
    events = []

    def record(path, report):
        assert path == directory
        assert read_json(directory / 'thin_media_report.json', {}) == report
        events.append('recorded after report')
        original(path, report)

    monkeypatch.setattr(history, 'record_render', record)
    return events


def test_asr_failure_still_charges_completed_video_after_report(episode, monkeypatch):
    directory, clip, _ = episode
    calls = []
    r = runner(directory, clip, completed_provider(calls))
    def fail(*args):
        assert history.generation_counts(directory) == {'c': 1}
        assert managed.generated_counts(history.load(directory)) == {}
        raise subprocess.CalledProcessError(2, ['missing-asr-helper'])
    r.analyse_clip = fail
    events = assert_report_then_history(monkeypatch, directory)
    report = r.run()
    assert report['status'] == 'clips_failed'
    assert report['clips'][0]['attempts'] == [] and report['clips'][0]['selected'] is None
    assert calls == ['task-1'] and r.context.state.remaining == {'c': 2}
    assert managed.generated_counts(history.load(directory)) == {'c': 1}
    assert events == ['recorded after report']


@pytest.mark.parametrize('stop_at', ['before_asr', 'after_download'])
def test_interrupted_generation_is_charged_once_when_resumed_from_cache(episode, stop_at):
    directory, clip, plan = episode
    calls = []
    generate = completed_provider(calls)
    def interrupted_provider(*args, **kwargs):
        generate(*args, **kwargs)
        raise SystemExit('process stopped after download')
    r = runner(directory, clip, interrupted_provider if stop_at == 'after_download' else generate)
    if stop_at == 'before_asr':
        r.analyse_clip = lambda *a: (_ for _ in ()).throw(SystemExit('stopped before ASR'))
    with pytest.raises(SystemExit):
        r.process_clip(r.context.clip_plan['clips'][0])
    assert managed.generated_counts(history.load(directory)) == {}
    assert history.generation_counts(directory) == {'c': 1}
    assert not (directory / 'thin_media_report.json').exists()
    add_trial(directory, plan)
    resumed = runner(directory, clip, lambda *a, **kw: pytest.fail('cache resume must not submit again'))
    assert resumed.context.state.remaining == {'c': 2}
    report = resumed.run()
    assert report['clips'][0]['selected']['generated_this_run'] is False
    state = history.load(directory)
    assert managed.generated_counts(state) == {'c': 1}
    assert state['trials'][0]['renders'][0]['clips']['c']['generated_takes']
    assert not state['trials'][1]['renders'][0]['clips']['c']['generated_takes']
    history.record_render(directory, report)
    assert managed.generated_counts(history.load(directory)) == {'c': 1}
    assert calls == ['task-1']


def test_pending_owned_job_preserves_original_trial_when_request_is_rewritten(episode):
    directory, clip, plan = episode
    jobs = []
    def pending(prompt, image, output, **kwargs):
        jobs.append('task-1')
        task(output, 'task-1')
        raise SystemExit('stopped while task was running')
    r = runner(directory, clip, pending)
    with pytest.raises(SystemExit):
        r.process_clip(r.context.clip_plan['clips'][0])
    assert history.generation_counts(directory) == {}
    add_trial(directory, plan)
    def resume(prompt, image, output, **kwargs):
        assert read_json(output.parent / 'request.json')[history.GENERATION_FIELD]['trial'] == 1
        assert read_json(output.with_suffix('.mp4.task.json'))['task_id'] == 'task-1'
        output.write_bytes(b'completed saved job')
        return output
    resumed = runner(directory, clip, resume)
    resumed.run()
    state = history.load(directory)
    assert jobs == ['task-1']
    assert managed.generated_counts(state) == {'c': 1}
    assert state['trials'][0]['renders'][0]['clips']['c']['generated_takes']
    assert not state['trials'][1]['renders'][0]['clips']['c']['generated_takes']


@pytest.mark.parametrize('has_video', [False, True])
def test_legacy_cached_material_is_never_retroactively_owned(episode, has_video):
    directory, clip, _ = episode
    r = runner(directory, clip, lambda *a, **kw: pytest.fail('placeholder'))
    attempt = r.context.work / 'clips/c/attempt_01'
    attempt.mkdir(parents=True)
    request = generation.build_request(r.context, clip, 1)[0]
    (attempt / 'request.json').write_text(json.dumps(request))
    task(attempt / 'clip.mp4', 'legacy-task')
    if has_video:
        (attempt / 'clip.mp4').write_bytes(b'preexisting baseline')
    else:
        def old_job(prompt, image, output, **kwargs):
            output.write_bytes(b'completed legacy task')
            return output
        r.context.provider.create_video = old_job
    report = r.run()
    assert report['clips'][0]['selected']['generated_this_run'] is False
    assert history.GENERATION_FIELD not in read_json(attempt / 'request.json')
    assert history.generation_counts(directory) == {}
    assert r.context.state.remaining == {'c': 3}


def test_provider_replacing_legacy_pending_task_is_a_genuine_new_owned_generation(episode):
    directory, clip, _ = episode
    calls = []
    r = runner(directory, clip, completed_provider(calls, 'new-task'))
    attempt = r.context.work / 'clips/c/attempt_01'
    attempt.mkdir(parents=True)
    (attempt / 'request.json').write_text(json.dumps(generation.build_request(r.context, clip, 1)[0]))
    task(attempt / 'clip.mp4', 'forgotten-old-task')
    r.run()
    assert calls == ['new-task']
    assert history.generation_counts(directory) == {'c': 1}
    assert read_json(attempt / 'request.json')[history.GENERATION_FIELD]['trial'] == 1


def test_owned_task_without_a_completed_nonempty_video_does_not_spend_budget(episode):
    directory, clip, _ = episode
    def pending(prompt, image, output, **kwargs):
        task(output, 'pending-task')
        output.write_bytes(b'')
        raise RuntimeError('task still running; no material downloaded')
    r = runner(directory, clip, pending)
    report = r.run()
    assert report['status'] == 'clips_failed'
    assert history.generation_counts(directory) == {}
    assert r.context.state.remaining == {'c': 3}


def test_archived_and_redownloaded_material_keeps_one_canonical_generation(episode):
    directory, clip, _ = episode
    r = runner(directory, clip, completed_provider([]))
    video = r.generate_clip(clip, 1)
    receipt = read_json(video.parent / 'request.json')[history.GENERATION_FIELD]
    archive = history.archived_take(directory, 'c', video)
    assert Path(archive['video']).stat().st_ino != video.stat().st_ino
    assert history.generation_counts(directory) == {'c': 1}
    # Download the same saved task to a new inode, as after a local loss.
    replacement = video.with_name('replacement.mp4')
    replacement.write_bytes(b'generated video')
    replacement.replace(video)
    completed = history.complete_generation(directory, 'c', video, duration=5.0)
    assert completed['take'] == receipt['take']
    assert completed['file_take'] != receipt['file_take']
    assert history.generation_counts(directory) == {'c': 1}
    video.unlink()
    assert history.generation_counts(directory) == {'c': 1}
    report = {'clip_plan_fingerprint': plan_fingerprint(r.context.clip_plan), 'review_feedback': {}, 'clips': []}
    history.record_render(directory, report)
    history.record_render(directory, report)
    assert managed.generated_counts(history.load(directory)) == {'c': 1}
    assert history.generation_counts(directory) == {'c': 1}


def test_pending_completion_enforces_cap_before_another_request(episode):
    directory, clip, _ = episode
    calls = []
    r = runner(directory, clip, completed_provider(calls))
    for attempt in range(1, 4):
        # Separate real retake requests; cache matching must not absorb them.
        r.generate_clip({**clip, 'repair_take': attempt}, attempt)
    assert managed.generated_counts(history.load(directory)) == {}
    assert history.generation_counts(directory) == {'c': 3}
    resumed = runner(directory, clip, lambda *a, **kw: pytest.fail('budget exhausted'))
    reviewed._load_current(resumed)
    assert resumed.context.state.remaining == {'c': 0}
    with pytest.raises(ValueError, match='retry budget used'):
        resumed.generate_clip({**clip, 'repair_take': 4}, 4)
    assert len(calls) == 3


def test_owned_completion_is_counted_even_if_current_report_has_a_new_plan(episode):
    directory, clip, _ = episode
    r = runner(directory, clip, completed_provider([]))
    r.generate_clip(clip, 1)
    report = {'clip_plan_fingerprint': 'later plan', 'review_feedback': {'c': 'later note'}, 'clips': []}
    history.record_render(directory, report)
    state = history.load(directory)
    assert managed.generated_counts(state) == {'c': 1}
    assert state['trials'][0]['renders'][0]['candidate_video'] is None


def test_renumbered_pending_clip_keeps_its_original_generation_owner(episode):
    directory, clip, _ = episode
    r = runner(directory, clip, lambda *a, **kw: pytest.fail('not submitted here'))
    output = r.context.work / 'clips/c/attempt_01/clip.mp4'
    output.parent.mkdir(parents=True)
    request = generation.build_request(r.context, clip, 1)[0]
    owned = history.generation_request(directory, output, request,
                                       expected_plan=plan_fingerprint(r.context.clip_plan), expected_notes={})
    (output.parent / 'request.json').write_text(json.dumps(owned))
    task(output, 'task-before-renumbering')
    renamed = output.parent.parent.with_name('renumbered')
    output.parent.parent.rename(renamed)
    output = renamed / 'attempt_01/clip.mp4'
    request['clip_id'] = 'renumbered'
    resumed = history.generation_request(directory, output, request, expected_plan='later plan', expected_notes={})
    assert resumed[history.GENERATION_FIELD] == owned[history.GENERATION_FIELD]
    (output.parent / 'request.json').write_text(json.dumps(resumed))
    output.write_bytes(b'saved task completed after the renumbering')
    history.complete_generation(directory, 'renumbered', output, duration=5.0)
    assert history.generation_counts(directory) == {'c': 1}
    report = {'clip_plan_fingerprint': 'later plan', 'review_feedback': {}, 'clips': []}
    history.record_render(directory, report)
    assert managed.generated_counts(history.load(directory)) == {'c': 1}
