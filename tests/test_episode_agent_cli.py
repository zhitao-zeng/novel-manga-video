"""Episode entrypoint tests without Docker, models or production services."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from novel_manga.application.agents.episode import cli, flow
from novel_manga.application.agents.episode.context import create_context
from novel_manga.util import atomic_write_json, read_json
from support.episode_runtime import episode_context as ctx, sandbox_config as config, fake_attempt, install_fake_runtime


def test_prepare_and_status_use_no_model_and_preserve_source(ctx, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, 'create_context', lambda *args, **kwargs: ctx)
    monkeypatch.setattr(cli, 'prepare_inputs', lambda value: calls.append(value))
    monkeypatch.setattr(flow, 'run_session', lambda *a, **k: pytest.fail('model called'))
    monkeypatch.setattr(flow, 'EpisodeBridge', lambda *a, **k: pytest.fail('bridge started'))
    marker = ctx.source_novel_dir / 'controller.json'
    marker.write_text('{"paused": true}')
    before = {p: p.read_bytes() for p in ctx.source_novel_dir.rglob('*') if p.is_file()}
    assert cli.main(['prepare', '--novel-dir', str(ctx.source_novel_dir), '--episode', '1']) == 0
    assert calls == [ctx]
    assert cli.main(['status', '--novel-dir', str(ctx.source_novel_dir), '--episode', '1']) == 0
    assert calls == [ctx]
    assert {p: p.read_bytes() for p in ctx.source_novel_dir.rglob('*') if p.is_file()} == before
    assert not ctx.run_dir.exists()
    capsys.readouterr()


def test_input_prompts_keep_script_authority(ctx):
    source = flow.initial_prompt(ctx)
    script = flow.initial_prompt(replace(ctx, input_mode='script', target_seconds=(63, 63)))
    assert 'complete original chapter' in source
    assert 'screenplay' in source and 'output/episode.json' in source
    assert '120' in source and '150' in source
    assert 'authoritative' in script and 'output/requests.json' in script
    assert 'dialogue' in script and 'timing' in script and 'reference' in script
    assert 'Do not replan' in script
    assert '120–150' not in script
    for prompt in (source, script):
        assert '/work/tools/episode.py' in prompt
        assert 'finish' in prompt and 'review' in prompt


def test_batch_is_sequential_and_uses_existing_chapter_parser(ctx, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, 'create_context', lambda novel, episode, **kw: calls.append(episode) or ctx)
    monkeypatch.setattr(cli, 'run_episode', lambda context, **kw: {'status': 'finished', 'passed': True, 'exit_code': 0})
    assert cli.main(['run', '--novel-dir', str(ctx.source_novel_dir), '--chapters', '3,1-2,2']) == 0
    assert calls == [1, 2, 3]
    assert len(json.loads(capsys.readouterr().out)['episodes']) == 3


def test_project_root_scoped_for_runtime_data_and_restored(ctx, monkeypatch, capsys):
    previous = os.environ.get('NOVEL_PROJECT_ROOT')
    seen = []
    monkeypatch.setattr(cli, 'create_context', lambda *a, **k: seen.append(os.environ.get('NOVEL_PROJECT_ROOT')) or ctx)
    monkeypatch.setattr(cli, 'read_status', lambda context: {'status': 'prepared'})
    assert cli.main(['status', '--novel-dir', str(ctx.source_novel_dir), '--episode', '1',
                     '--project-root', str(ctx.project_root)]) == 0
    assert seen == [str(ctx.project_root)]
    assert os.environ.get('NOVEL_PROJECT_ROOT') == previous
    capsys.readouterr()


def test_run_waits_for_background_then_resumes_same_session(ctx, monkeypatch, config):
    calls = install_fake_runtime(monkeypatch, ctx)
    sessions = []
    submitted = threading.Event()
    released = threading.Event()
    original_tools = flow.EpisodeTools

    class BackgroundTools(original_tools):
        def dispatch(self, action, payload):
            if action == 'render':
                submitted.set()
                assert released.wait(3)
            return super().dispatch(action, payload)

    monkeypatch.setattr(flow, 'EpisodeTools', BackgroundTools)
    created = []
    original_bridge = flow.EpisodeBridge

    def make_bridge(*args):
        instance = original_bridge(*args)
        created.append(instance)
        return instance

    monkeypatch.setattr(flow, 'EpisodeBridge', make_bridge)

    def session(work, prompt, **kwargs):
        sessions.append((prompt, kwargs))
        atomic_write_json(work / 'episode-session.json', {'session_id': 'same-session'})
        if len(sessions) == 1:
            answer = created[0].handle('render', {})
            assert answer['status'] == 'accepted' and answer['yield'] is True
            assert not submitted.is_set()
            threading.Timer(0.05, released.set).start()
        else:
            assert released.is_set()
            assert created[0].handle('finish', {})['status'] == 'accepted'
        return fake_attempt(ctx, len(sessions))

    monkeypatch.setattr(flow, 'run_session', session)
    result = flow.run_episode(ctx, config=config)
    assert result['passed'] is True and result['exit_code'] == 0
    assert len(sessions) == 2
    assert [kw['resume'] for _, kw in sessions] == [False, True]
    assert all(kw['enable_tools'] is True for _, kw in sessions)
    assert all(kw['expected_output'] == 'episode.json' for _, kw in sessions)
    assert calls.count('render') == 1 and calls.count('finish') == 1
    assert not (ctx.work_dir / 'episode-tools.sock').exists()


def test_old_finish_manifest_does_not_complete_new_invocation(ctx, monkeypatch, config):
    install_fake_runtime(monkeypatch, ctx)
    (ctx.work_dir / 'input').mkdir(parents=True)
    atomic_write_json(ctx.work_dir / 'input/latest-finish.json', {
        'passed': True, 'author_session': {'session_id': 'same-session'}})
    atomic_write_json(ctx.work_dir / 'episode-session.json', {'session_id': 'same-session'})
    sessions = []

    def session(work, prompt, **kwargs):
        sessions.append(kwargs)
        return fake_attempt(ctx, len(sessions))

    monkeypatch.setattr(flow, 'run_session', session)
    result = flow.run_episode(ctx, config=config)
    assert result['status'] == 'blocked' and result['passed'] is False
    assert result['exit_code'] != 0 and len(sessions) == 2
    assert all(kw['resume'] for kw in sessions)
    assert read_json(ctx.work_dir / 'input/latest-finish.json')['passed'] is True


def test_nonzero_session_stops_and_restores_process_environment(ctx, monkeypatch, config):
    install_fake_runtime(monkeypatch, ctx)
    before = dict(os.environ)
    monkeypatch.setattr(flow, 'runtime_environment', lambda *a: {**os.environ, 'EPISODE_TEMP_SETTING': 'test'})
    monkeypatch.setattr(flow, 'run_session', lambda *a, **kw: fake_attempt(ctx, 1, 125))
    result = flow.run_episode(ctx, config=config)
    assert result['status'] == 'blocked' and result['passed'] is False
    assert result['session_exit'] == 125
    assert dict(os.environ) == before
    assert not (ctx.work_dir / 'episode-tools.sock').exists()


def test_script_run_passes_requests_contract_and_preserves_controller(ctx, monkeypatch, config):
    ctx = replace(ctx, input_mode='script', target_seconds=(63, 63))
    install_fake_runtime(monkeypatch, ctx)
    marker = ctx.source_novel_dir / 'controller.json'
    marker.write_text('{"paused": true}')
    sessions = []

    def session(work, prompt, **kwargs):
        sessions.append(kwargs)
        return fake_attempt(ctx, len(sessions))

    monkeypatch.setattr(flow, 'run_session', session)
    result = flow.run_episode(ctx, config=config)
    assert result['status'] == 'blocked'
    assert all(kw['expected_output'] == 'requests.json' for kw in sessions)
    assert marker.read_text() == '{"paused": true}'


def test_environment_uses_configured_qa_and_respects_explicit_h3(ctx, monkeypatch):
    settings = {'base_url': 'http://qa.service:8038', 'model': 'qa-model', 'key_var': 'QA_KEY'}
    monkeypatch.setattr(flow, 'ENDPOINTS', {'known': {
        'QWEN38_LOCAL_BASE_URL': 'http://qa.service:8038/v1', 'QWEN38_LOCAL_MODEL': 'qa-model',
        'QWEN38_LOCAL_API_KEY_VAR': 'QA_KEY'}})
    monkeypatch.setattr(flow, 'environment', lambda root: {'NOVEL_LOCAL_H3_URL': 'http://explicit-h3',
                                                        'NOVEL_REVIEW_CAST_VIDEO': '0'})
    monkeypatch.setattr(flow, 'repair_environment', lambda *a: pytest.fail('explicit H3 overwritten'))
    env = flow.runtime_environment(ctx, settings)
    assert env['NOVEL_LOCAL_H3_URL'] == 'http://explicit-h3'
    assert env['QWEN38_LOCAL_MODEL'] == 'qa-model'
    assert env['QWEN38_LOCAL_BASE_URL'] == 'http://qa.service:8038/v1'
    assert env['QWEN38_LOCAL_API_KEY_VAR'] == 'QA_KEY'
    assert env['SECOND_REVIEW_JUDGE'] == env['NOVEL_REVIEW_VIDEO_JUDGE'] == 'known'
    assert env['NOVEL_REVIEW_CAST_VIDEO'] == '0'
    assert 'NOVEL_QC_IGNORE' not in env


def test_environment_uses_existing_h3_pool_configuration(ctx, monkeypatch):
    monkeypatch.setattr(flow, 'environment', lambda root: {})
    monkeypatch.setattr(flow, 'repair_environment', lambda root, legacy: ({
        'NOVEL_LOCAL_H3_URL': 'pool', 'NOVEL_INFLIGHT_POOL': 'configured-pool'},
        {'pool': 'configured-pool', 'inflight': 7}))
    settings = {'base_url': 'http://configured-agent', 'model': 'qa-model', 'key_var': 'QA_KEY'}
    monkeypatch.setattr(flow, 'ENDPOINTS', {'configured': {
        'QWEN38_LOCAL_BASE_URL': 'http://configured-agent/v1', 'QWEN38_LOCAL_MODEL': 'qa-model',
        'QWEN38_LOCAL_API_KEY_VAR': 'QA_KEY'}})
    env = flow.runtime_environment(ctx, settings)
    assert env['NOVEL_LOCAL_H3_URL'] == 'pool' and env['NOVEL_INFLIGHT_POOL'] == 'configured-pool'
    assert env['QWEN38_LOCAL_BASE_URL'] == 'http://configured-agent/v1'


def test_environment_refuses_unconfigured_judge_instead_of_localhost(ctx, monkeypatch):
    monkeypatch.setattr(flow, 'environment', lambda root: {'NOVEL_LOCAL_H3_URL': 'pool'})
    monkeypatch.setattr(flow, 'ENDPOINTS', {})
    with pytest.raises(ValueError, match='no existing QA preset'):
        flow.runtime_environment(ctx, {'base_url': 'http://new-agent', 'model': 'custom', 'key_var': 'KEY'})


def test_qa_defaults_replace_old_dotenv_localhost_but_preserve_process_override(ctx, monkeypatch):
    defaults = {'QWEN38_LOCAL_BASE_URL': 'http://qa-configured/v1',
                'QWEN38_LOCAL_MODEL': 'configured-qa', 'QWEN38_LOCAL_API_KEY_VAR': 'QA_KEY'}
    for key in (*defaults, 'SECOND_REVIEW_JUDGE', 'NOVEL_REVIEW_VIDEO_JUDGE'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(flow, 'ENDPOINTS', {'configured': defaults})
    monkeypatch.setattr(flow, 'environment', lambda root: {
        'NOVEL_LOCAL_H3_URL': 'pool', 'QWEN38_LOCAL_BASE_URL': 'http://127.0.0.1:dead/v1',
        'SECOND_REVIEW_JUDGE': 'local', 'NOVEL_REVIEW_VIDEO_JUDGE': 'local'})
    config = {'base_url': 'http://qa-configured', 'model': 'configured-qa', 'key_var': 'QA_KEY'}
    env = flow.runtime_environment(ctx, config)
    assert env['QWEN38_LOCAL_BASE_URL'] == defaults['QWEN38_LOCAL_BASE_URL']
    assert env['SECOND_REVIEW_JUDGE'] == env['NOVEL_REVIEW_VIDEO_JUDGE'] == 'configured'
    monkeypatch.setenv('SECOND_REVIEW_JUDGE', 'explicit')
    assert flow.runtime_environment(ctx, config)['SECOND_REVIEW_JUDGE'] == 'explicit'


def test_operation_continuations_are_bounded_and_keep_same_session(ctx, monkeypatch, config):
    install_fake_runtime(monkeypatch, ctx)
    calls = []
    # Equal-size edits can share a filesystem timestamp. Progress must come
    # from the changed draft, rather than how fast this fake session returns.
    monkeypatch.setattr(flow, '_file_stamp', lambda path: (1, 16) if path.exists() else None)

    def session(work, prompt, **kwargs):
        calls.append(kwargs)
        atomic_write_json(work / 'episode-session.json', {'session_id': 'same-session'})
        atomic_write_json(work / 'output/episode.json', {'revision': len(calls)})
        return fake_attempt(ctx, len(calls))

    monkeypatch.setattr(flow, 'run_session', session)
    result = flow.run_episode(ctx, config=config)
    assert result['status'] == 'blocked' and result['passed'] is False
    assert len(calls) == 3 and [kw['resume'] for kw in calls] == [False, True, True]
    assert 'continuation limit' in result['reason']
    assert all(0 < kw['timeout'] <= config['timeout_seconds'] for kw in calls)


def test_progress_detects_equal_size_output_edits_with_unchanged_timestamp(ctx):
    path = ctx.work_dir / 'output/episode.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"revision":1}')
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    before = flow._progress(ctx, {})
    path.write_bytes(b'{"revision":2}')
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    assert flow._progress(ctx, {}) != before


def test_unchanged_output_content_is_not_progress_when_timestamp_changes(ctx):
    path = ctx.work_dir / 'output/episode.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"revision":1}')
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    before = flow._progress(ctx, {})
    os.utime(path, ns=(2_000_000_000, 2_000_000_000))
    assert flow._progress(ctx, {}) == before


def test_session_exception_waits_for_accepted_host_job_before_cleanup(ctx, monkeypatch, config):
    install_fake_runtime(monkeypatch, ctx)
    entered, released = threading.Event(), threading.Event()
    original_tools = flow.EpisodeTools
    original_bridge = flow.EpisodeBridge
    bridges = []

    class PendingTools(original_tools):
        def dispatch(self, action, payload):
            if action == 'render':
                entered.set()
                assert released.wait(3)
            return super().dispatch(action, payload)

    def make_bridge(*args):
        instance = original_bridge(*args)
        bridges.append(instance)
        return instance

    def session(work, prompt, **kwargs):
        bridges[0].handle('render', {})
        assert not entered.is_set()
        threading.Timer(0.05, released.set).start()
        raise OSError('test CLI startup error')

    monkeypatch.setattr(flow, 'EpisodeTools', PendingTools)
    monkeypatch.setattr(flow, 'EpisodeBridge', make_bridge)
    monkeypatch.setattr(flow, 'run_session', session)
    result = flow.run_episode(ctx, config=config)
    assert result['status'] == 'blocked' and 'test CLI startup error' in result['reason']
    assert released.is_set() and bridges[0]._future.done()
    assert not (ctx.work_dir / 'episode-tools.sock').exists()


def test_real_prepare_then_status_need_no_credentials_or_services(ctx, monkeypatch, capsys):
    atomic_write_json(ctx.source_episode_dir / 'segments.json', [{'segment_id': 's1', 'text': '完整原文。'}])
    atomic_write_json(ctx.source_novel_dir / 'story_bible.json', {'novel_id': 'book', 'characters': []})
    monkeypatch.setattr(flow, 'run_session', lambda *a, **kw: pytest.fail('model called'))
    args = ['--novel-dir', str(ctx.source_novel_dir), '--episode', '1',
            '--output-root', str(ctx.run_dir.parents[1]), '--project-root', str(ctx.project_root)]
    assert cli.main(['prepare', *args]) == 0
    prepared = json.loads(capsys.readouterr().out)['episodes'][0]
    run = Path(prepared['run_dir'])
    assert (run / 'sandbox/input/source.txt').read_text() == '完整原文。'
    before = {p: p.read_bytes() for p in run.rglob('*') if p.is_file()}
    assert cli.main(['status', *args]) == 0
    assert {p: p.read_bytes() for p in run.rglob('*') if p.is_file()} == before
    capsys.readouterr()


def test_entrypoint_help_runs_without_services_or_credentials():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / 'scripts/episode_agent.py'), '--help'],
                            capture_output=True, text=True, cwd=root)
    assert result.returncode == 0, result.stderr
    assert 'prepare' in result.stdout and 'run' in result.stdout and 'status' in result.stdout


@pytest.mark.parametrize('args', [
    ['prepare', '--novel-dir', 'book'],
    ['prepare', '--novel-dir', 'book', '--episode', '1', '--chapters', '1-2'],
])
def test_cli_requires_exactly_one_episode_selection(args):
    with pytest.raises(SystemExit) as exc:
        cli.main(args)
    assert exc.value.code == 2


def test_assigned_multi_endpoint_judge_is_resolved_from_actual_runtime(ctx, monkeypatch):
    group = 'http://one.model:9000/v1,http://two.model:9000/v1'
    values = {'QWEN38_LOCAL_BASE_URL': group, 'QWEN38_LOCAL_MODEL': 'qa-model',
              'QWEN38_LOCAL_API_KEY_VAR': 'QA_KEY', 'NOVEL_LOCAL_H3_URL': 'http://h3'}
    for key in ('QWEN38_LOCAL_BASE_URL', 'QWEN38_LOCAL_MODEL', 'QWEN38_LOCAL_API_KEY_VAR'):
        monkeypatch.setenv(key, values[key])
    monkeypatch.delenv('SECOND_REVIEW_JUDGE', raising=False)
    monkeypatch.delenv('NOVEL_REVIEW_VIDEO_JUDGE', raising=False)
    monkeypatch.setattr(flow, 'environment', lambda root: values.copy())
    monkeypatch.setattr(flow, 'ENDPOINTS', {'group-qa': values})
    settings = {'base_url': 'http://one.model:9000', 'model': 'qa-model', 'key_var': 'QA_KEY'}
    env = flow.runtime_environment(ctx, settings)
    assert env['SECOND_REVIEW_JUDGE'] == env['NOVEL_REVIEW_VIDEO_JUDGE'] == 'group-qa'
