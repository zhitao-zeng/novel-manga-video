"""Daily production stops new submissions at quota exhaustion and resumes saved work."""
import json
from types import SimpleNamespace

import httpx
import pytest

from support.generation import batch_stub, paid_episode, provider_with
from novel_manga.application import configuration
from novel_manga.application.production import (
    conductor_capacity, conductor_dispatch, conductor_flow, conductor_state,
    conductor_workers, render, runs,
)
from novel_manga.providers import phanrouter_tasks


@pytest.fixture
def quota_file(tmp_path, monkeypatch):
    path = tmp_path / 'quota-exhausted'
    monkeypatch.setenv('NOVEL_QUOTA_STOP_FILE', str(path))
    monkeypatch.setattr(phanrouter_tasks.time, 'sleep', lambda _: None)
    return path


def test_quota_refusal_stops_later_video_and_image_submissions(tmp_path, quota_file):
    calls = []
    def handle(request):
        calls.append(request.method)
        return httpx.Response(403, json={'code': 'quota_not_enough'})
    provider = provider_with(handle)
    try:
        with pytest.raises(RuntimeError, match='quota_not_enough'):
            provider.create_video('scene', None, tmp_path / 'first.mp4', 4)
        assert quota_file.exists()
        with pytest.raises(RuntimeError, match='quota_not_enough'):
            provider.create_video('next scene', None, tmp_path / 'next.mp4', 4)
        with pytest.raises(RuntimeError, match='quota_not_enough'):
            provider.create_image('next card', tmp_path / 'next.jpeg')
    finally:
        provider.client.close()
    assert calls == ['POST']
    assert not list(tmp_path.glob('*.task.json'))


def test_already_accepted_video_is_polled_and_downloaded_after_quota_stop(tmp_path, quota_file):
    calls = []
    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == 'POST':
            # Another concurrent request exhausts quota after this one was accepted.
            quota_file.touch()
            return httpx.Response(200, json={'task_id': 'accepted'})
        if request.url.path.endswith('/accepted'):
            return httpx.Response(200, json={'data': {
                'status': 'succeeded', 'video_url': 'https://cloud.test/artifact',
                'usage': {'completion_tokens': 10},
            }})
        return httpx.Response(200, content=b'paid video')
    provider = provider_with(handle)
    output = tmp_path / 'clip.mp4'
    try:
        provider.create_video('scene', None, output, 4)
        provider.create_video('scene', None, output, 4)
    finally:
        provider.client.close()
    assert output.read_bytes() == b'paid video'
    assert sum(method == 'POST' for method, _ in calls) == 1
    assert sum(path.endswith('/accepted') for _, path in calls) == 2
    assert json.loads(output.with_suffix('.mp4.task.json').read_text())['task_id'] == 'accepted'


def test_throttle_retry_stops_when_another_worker_exhausts_quota(quota_file, monkeypatch):
    calls = []
    def submit():
        calls.append(1)
        response = httpx.Response(429, request=httpx.Request('POST', 'https://cloud.test/tasks'))
        response.raise_for_status()
    monkeypatch.setattr(phanrouter_tasks.time, 'sleep', lambda _: quota_file.touch())
    with pytest.raises(RuntimeError, match='quota_not_enough'):
        phanrouter_tasks.submit_once(submit)
    assert calls == [1]


def daily_conductor(tmp_path):
    key = {'name': 'sd25', 'model': 'sd2.5', 'key_var': 'PHANROUTER_API_KEY',
           'clip_cap': 30, 'parallel': 1, 'inflight': {'min': 1, 'max': 2, 'start': 1}}
    config = {'novel_dir': str(tmp_path / 'nov'), 'tmp_dir': str(tmp_path / 'controller'),
              'keys': [key], 'ranges': [{'chapters': '1-1', 'plan_mode': 30}],
              'stop_on_quota_exhausted': True, 'qwen': {'urls': []},
              'planning': {'block_size': 1, 'blocks_min': 0, 'blocks_max': 0, 'margin': 0}}
    (tmp_path / 'nov').mkdir(exist_ok=True)
    return conductor_flow.Conductor(config, dry_run=False)


def test_controller_drains_workers_then_exits_without_dispatch_or_signals(tmp_path, monkeypatch):
    c = daily_conductor(tmp_path)
    c.quota_stop_file.touch()
    worker = SimpleNamespace(pid=123, returncode=None)
    worker.poll = lambda: worker.returncode
    c.procs['lane_sd25'] = worker
    stats = {'total': 1, 'planned': 1, 'done': 0, 'blocked': [],
             'renderable': [1], 'pending': [1], 'unreviewed': []}
    monkeypatch.setattr(conductor_state, 'range_stats', lambda *args: stats)
    def no_dispatch(*args):
        pytest.fail('new production work was dispatched after quota exhaustion')
    monkeypatch.setattr(conductor_dispatch, 'tick_lanes', no_dispatch)
    monkeypatch.setattr(conductor_dispatch, 'tick_planning', no_dispatch)
    monkeypatch.setattr(conductor_workers.os, 'killpg', lambda *args: pytest.fail('in-flight worker killed'))
    drained = []
    def finish_work(_):
        state = json.loads((c.tmp / 'state.json').read_text())
        assert state['status'] == 'draining_quota' and state['workers'] == {'lane_sd25': 123}
        drained.append(1)
        worker.returncode = 0
    monkeypatch.setattr(conductor_flow.time, 'sleep', finish_work)
    c.run(once=False)
    assert drained == [1]
    state = json.loads((c.tmp / 'state.json').read_text())
    assert state['status'] == 'waiting_quota'
    assert state['work']['pending'] == 1 and state['stop_reason'] == 'quota_not_enough'


def test_next_controller_run_clears_quota_stop_and_preserves_episode_cache(tmp_path):
    previous = daily_conductor(tmp_path)
    previous.quota_stop_file.touch()
    cached = tmp_path / 'nov' / 'paid.mp4'
    cached.write_bytes(b'paid video')
    resumed = daily_conductor(tmp_path)
    assert not resumed.quota_stop_file.exists()
    assert cached.read_bytes() == b'paid video'


def test_daily_controller_ignores_old_quota_log_lines(tmp_path, monkeypatch):
    c = daily_conductor(tmp_path)
    patterns = []
    def old_lines(controller, chapters, pattern, seconds):
        patterns.append(pattern)
        return 9 if pattern == 'quota_not_enough' else 0
    monkeypatch.setattr(conductor_state, 'recent_lines', old_lines)
    conductor_capacity.tick_aimd(c, 'sd25', c.keys['sd25'], [1])
    assert 'quota_not_enough' not in patterns
    assert c.lanes['sd25']['parked_until'] == 0


def test_workers_receive_the_current_run_quota_stop_file(tmp_path, monkeypatch):
    c = daily_conductor(tmp_path)
    observed = []
    def spawn(command, **kwargs):
        observed.append(kwargs['env'])
        return SimpleNamespace(pid=123)
    monkeypatch.setattr(conductor_workers.subprocess, 'Popen', spawn)
    conductor_workers.spawn(c, 'lane_sd25', ['python', 'worker.py'])
    assert observed[0]['NOVEL_QUOTA_STOP_FILE'] == str(c.quota_stop_file)


def test_book_configuration_passes_daily_quota_policy_to_controller(tmp_path):
    config = {'resources': {}, 'novels': [{'id': 'nov', 'ranges': [],
                                          'stop_on_quota_exhausted': True}]}
    assert configuration.config_for_novel(config, 'nov', root=tmp_path)['stop_on_quota_exhausted']


def test_queued_episode_does_not_build_assets_or_spend_a_run_after_quota_stop(tmp_path, monkeypatch, quota_file):
    directory = paid_episode(tmp_path)
    batch = batch_stub(tmp_path, monkeypatch, ['pending'])
    monkeypatch.setattr(batch, 'prepare_cards', lambda *args: pytest.fail('new assets prepared'))
    quota_file.touch()
    render.render(batch, 1)
    assert batch.rows[1]['render'] == 'waiting_quota' and not batch.commands
    assert runs.render_runs(directory) == 0


@pytest.mark.parametrize('failure, expected_runs', [
    ('RuntimeError: quota_not_enough', 2),
    ('RuntimeError: genuine generation failure', 3),
    (None, 3),
])
def test_quota_only_interruption_keeps_retry_allowance_but_real_failures_spend_it(
    tmp_path, monkeypatch, quota_file, failure, expected_runs,
):
    from novel_manga.application.preparation import readiness
    directory = paid_episode(tmp_path)
    plan = json.loads((directory / 'clip_plan.json').read_text())
    monkeypatch.setattr(readiness, 'inspect_episode', lambda *args, **kwargs: (plan, {}))
    monkeypatch.setattr(readiness, 'save_check', lambda *args: None)
    runs.count_run(directory)
    runs.count_run(directory)
    batch = batch_stub(tmp_path, monkeypatch, ['pending', 'clips_failed', 'clips_failed'])
    def stopped_run(command, log):
        batch.commands.append(command)
        quota_file.touch()
        report = {'failed_clips': ['clip_01'], 'gate_failed_clips': [],
                  'clips': [{'clip_id': 'clip_01', 'error': failure}]}
        (directory / 'thin_media_report.json').write_text(json.dumps(report))
        return 1, failure or 'generation failed'
    monkeypatch.setattr(batch, 'run', stopped_run)
    render.render(batch, 1)
    assert len(batch.commands) == 1
    assert runs.render_runs(directory) == expected_runs
    assert not (directory / '.render.lock').exists()


def test_permission_refusal_does_not_end_the_day(tmp_path, quota_file):
    def handle(request):
        return httpx.Response(403, json={'message': 'model permission denied'})
    provider = provider_with(handle)
    try:
        with pytest.raises(RuntimeError, match='permission denied'):
            provider.create_video('scene', None, tmp_path / 'first.mp4', 4)
    finally:
        provider.client.close()
    assert not quota_file.exists()


def test_quota_stop_allows_existing_video_review(tmp_path, monkeypatch, quota_file):
    paid_episode(tmp_path)
    batch = batch_stub(tmp_path, monkeypatch, ['pending'], review_only=True)
    reviewed = []
    monkeypatch.setattr(batch, 'review_episode', lambda chapter: reviewed.append(chapter))
    quota_file.touch()
    render.render(batch, 1)
    assert reviewed == [1] and not batch.commands


def test_quota_stop_prevents_new_local_image_generation(tmp_path, quota_file):
    from novel_manga.config import Settings
    from novel_manga.providers.local_qwen_image import LocalQwenImageProvider
    provider = LocalQwenImageProvider(Settings(), 'https://local.test')
    quota_file.touch()
    try:
        with pytest.raises(RuntimeError, match='quota_not_enough'):
            provider.create_image('card', tmp_path / 'card.jpeg')
    finally:
        provider.client.close()
    assert not (tmp_path / 'card.jpeg').exists()
