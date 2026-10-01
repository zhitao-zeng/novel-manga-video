"""Episode handoffs release resources while retaining session and operation ownership."""
import json
import os
from pathlib import Path
import threading
import time

import pytest

from novel_manga.application.agents.episode import flow
from novel_manga.llm.resources import model_slot
from novel_manga.util import atomic_write_json, read_json
from support.episode_runtime import episode_context as ctx, sandbox_config as config, fake_attempt, install_fake_runtime


def capture_bridge(monkeypatch):
    instances = []
    original = flow.EpisodeBridge

    def create(*args):
        instance = original(*args)
        instances.append(instance)
        return instance

    monkeypatch.setattr(flow, 'EpisodeBridge', create)
    return instances


def test_normal_tool_handoffs_can_exceed_three_invocations(ctx, config, monkeypatch):
    calls = install_fake_runtime(monkeypatch, ctx)
    bridges = capture_bridge(monkeypatch)
    actions = ['render', 'review', 'apply', 'render', 'review', 'finish']
    sessions = []

    def run(work, prompt, **options):
        sessions.append(options)
        atomic_write_json(work / 'episode-session.json', {'session_id': 'same-session'})
        answer = bridges[0].handle(actions[len(sessions) - 1], {})
        assert answer['yield'] is True and answer['status'] == 'accepted'
        return fake_attempt(ctx, len(sessions))

    monkeypatch.setattr(flow, 'run_session', run)
    result = flow.run_episode(ctx, config=config)
    assert result['passed'] and len(sessions) == 6
    assert [row['resume'] for row in sessions] == [False, True, True, True, True, True]
    assert [action for action in calls if action != 'status'] == actions
    assert result['fallback_invocations'] == 0
    assert result['session_id'] == 'same-session'


def test_render_and_review_start_after_agent_model_slot_is_released(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', json.dumps([{'base': config['base_url'], 'slots': 1}]))
    monkeypatch.setenv('NOVEL_MODEL_INFLIGHT_DIR', str(ctx.run_dir / 'model-slots'))
    bridges = capture_bridge(monkeypatch)
    original = flow.EpisodeTools
    entered, release, other_acquired = threading.Event(), threading.Event(), threading.Event()

    class Tools(original):
        def dispatch(self, action, payload):
            if action in {'render', 'review'}:
                # An independent reviewer using the same service must not deadlock.
                with model_slot(config['base_url'], deadline=time.monotonic() + 1):
                    pass
                if action == 'render':
                    entered.set()
                    assert release.wait(3)
            return super().dispatch(action, payload)

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    sessions = []

    def other_episode():
        assert entered.wait(3)
        # H3 waiting itself has no language-model lease.
        with model_slot(config['base_url'], deadline=time.monotonic() + 2):
            other_acquired.set()
        release.set()

    thread = threading.Thread(target=other_episode)
    thread.start()

    def run(work, prompt, **options):
        sessions.append(options)
        atomic_write_json(work / 'episode-session.json', {'session_id': 'same-session'})
        action = ['render', 'review', 'finish'][len(sessions) - 1]
        if len(sessions) == 1:
            assert not entered.is_set()
        bridges[0].handle(action, {})
        return fake_attempt(ctx, len(sessions))

    monkeypatch.setattr(flow, 'run_session', run)
    result = flow.run_episode(ctx, config=config)
    thread.join(3)
    assert not thread.is_alive() and other_acquired.is_set()
    assert result['passed'] and len(sessions) == 3


def test_pause_retains_accepted_operation_and_resumes_same_session_once(ctx, config, monkeypatch):
    calls = install_fake_runtime(monkeypatch, ctx)
    bridges = capture_bridge(monkeypatch)
    marker = ctx.run_dir / 'pause'
    monkeypatch.setenv('NOVEL_AGENT_PAUSE_FILE', str(marker))
    sessions = []

    def run(work, prompt, **options):
        sessions.append(options)
        atomic_write_json(work / 'episode-session.json', {'session_id': 'same-session'})
        if len(sessions) == 1:
            bridges[-1].handle('render', {})
            marker.write_text('paused')
        else:
            assert calls.count('render') == 1
            bridges[-1].handle('finish', {})
        return fake_attempt(ctx, len(sessions))

    monkeypatch.setattr(flow, 'run_session', run)
    paused = flow.run_episode(ctx, config=config)
    assert paused['status'] == 'paused' and paused['job']['status'] == 'accepted'
    assert calls.count('render') == 0
    deadline = paused['deadline']
    marker.unlink()
    finished = flow.run_episode(ctx, config=config)
    assert finished['passed'] and finished['deadline'] == deadline
    assert calls.count('render') == 1 and calls.count('finish') == 1
    assert [row['resume'] for row in sessions] == [False, True]


def test_duplicate_executor_does_not_start_a_model_or_change_budget(ctx, config, monkeypatch):
    ctx.source_episode_dir.mkdir(parents=True, exist_ok=True)
    lock = ctx.source_episode_dir / '.render.lock'
    lock.write_text(str(os.getpid()))
    monkeypatch.setattr(flow, 'run_session', lambda *a, **k: pytest.fail('duplicate model call'))
    with pytest.raises(RuntimeError, match='already owned'):
        flow.run_episode(ctx, config=config)
    assert lock.read_text() == str(os.getpid())
    assert not ctx.run_dir.exists()


def test_interrupted_apply_is_exposed_for_inspection_instead_of_replayed(ctx, config, monkeypatch):
    calls = install_fake_runtime(monkeypatch, ctx)
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(ctx.run_dir / 'agent-run.json', {
        'status': 'running', 'deadline': time.time() + 30,
        'job': {'id': 'job-2', 'action': 'apply', 'payload': {}, 'status': 'running'}})
    bridges = capture_bridge(monkeypatch)

    def run(work, prompt, **options):
        atomic_write_json(work / 'episode-session.json', {'session_id': 'same-session'})
        job = bridges[0].handle('status', {})['job']
        assert job['status'] == 'failed' and 'interrupted' in job['error']
        bridges[0].handle('finish', {})
        return fake_attempt(ctx, 1)

    monkeypatch.setattr(flow, 'run_session', run)
    assert flow.run_episode(ctx, config=config)['passed']
    assert calls.count('apply') == 0


@pytest.mark.parametrize('started', [False, True])
def test_restored_render_distinguishes_new_draft_from_submitted_media(ctx, config, monkeypatch, started):
    install_fake_runtime(monkeypatch, ctx)
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(ctx.run_dir / 'prepared.json', {'ready': True})
    atomic_write_json(ctx.run_dir / 'agent-run.json', {
        'status': 'running', 'deadline': time.time() + 30,
        'job': {'id': 'job-7', 'action': 'render', 'payload': {'file': 'output/new.json'},
                'status': 'running' if started else 'accepted'}})
    original = flow.EpisodeTools
    rendered = []

    class Tools(original):
        def _main_media_started(self):
            return started

        def dispatch(self, action, payload):
            if action == 'render':
                rendered.append(payload)
            return super().dispatch(action, payload)

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    bridges = capture_bridge(monkeypatch)

    def run(work, prompt, **options):
        assert rendered == ([{}] if started else [{'file': 'output/new.json'}])
        atomic_write_json(work / 'episode-session.json', {'session_id': 'same-session'})
        bridges[0].handle('finish', {})
        return fake_attempt(ctx, 1)

    monkeypatch.setattr(flow, 'run_session', run)
    assert flow.run_episode(ctx, config=config)['passed']


def test_completed_finish_receipt_recovers_after_crash_even_if_deadline_expired(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    (ctx.work_dir / 'input').mkdir(parents=True, exist_ok=True)
    atomic_write_json(ctx.work_dir / 'episode-session.json', {'session_id': 'same-session'})
    atomic_write_json(ctx.work_dir / 'input/latest-finish.json', {
        'passed': True, 'technical_passed': True, 'author_session': {'session_id': 'same-session'}})
    atomic_write_json(ctx.run_dir / 'agent-run.json', {
        'status': 'running', 'deadline': time.time() - 1,
        'job': {'id': 'job-8', 'action': 'finish', 'status': 'completed',
                'result': {'ok': True, 'passed': True}}})
    monkeypatch.setattr(flow, 'run_session', lambda *a, **k: pytest.fail('finished session resumed'))
    monkeypatch.setattr(flow, 'EpisodeBridge', lambda *a: pytest.fail('finished tools restarted'))
    result = flow.run_episode(ctx, config=config)
    assert result['passed'] and result['status'] == 'finished'
    assert result['deadline'] < time.time()


def test_pause_interrupts_waiting_for_model_without_invocation(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', json.dumps([{'base': config['base_url'], 'slots': 1}]))
    monkeypatch.setenv('NOVEL_MODEL_INFLIGHT_DIR', str(ctx.run_dir / 'slots'))
    marker = ctx.run_dir / 'pause'
    marker.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('NOVEL_AGENT_PAUSE_FILE', str(marker))
    monkeypatch.setattr(flow, 'run_session', lambda *a, **k: pytest.fail('paused model called'))
    with model_slot(config['base_url']):
        timer = threading.Timer(.05, lambda: marker.write_text('pause'))
        timer.start()
        result = flow.run_episode(ctx, config=config)
        timer.join(1)
    assert result['status'] == 'paused' and result['attempts'] == []


def test_accepted_finish_settles_before_expired_deadline_or_model_admission(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(ctx.work_dir / 'episode-session.json', {'session_id': 'same-session'})
    atomic_write_json(ctx.run_dir / 'agent-run.json', {
        'status': 'paused', 'deadline': time.time() - 1,
        'job': {'id': 'job-8', 'action': 'finish', 'payload': {}, 'status': 'accepted'}})
    monkeypatch.setattr(flow, 'run_session', lambda *a, **k: pytest.fail('settled finish resumed a model'))
    result = flow.run_episode(ctx, config=config)
    assert result['passed'] and result['status'] == 'finished'
    assert result['job']['status'] == 'completed'
    assert result['attempts'] == []


def test_written_pass_file_without_host_finish_receipt_does_not_complete_episode(ctx, config, monkeypatch):
    calls = install_fake_runtime(monkeypatch, ctx)
    sessions = []

    def run(work, prompt, **options):
        sessions.append(options)
        atomic_write_json(work / 'episode-session.json', {'session_id': 'same-session'})
        atomic_write_json(work / 'input/latest-finish.json', {
            'passed': True, 'author_session': {'session_id': 'same-session'}})
        return fake_attempt(ctx, len(sessions))

    monkeypatch.setattr(flow, 'run_session', run)
    result = flow.run_episode(ctx, config=config)
    assert result['passed'] is False and result['status'] == 'blocked'
    assert calls.count('finish') == 0
