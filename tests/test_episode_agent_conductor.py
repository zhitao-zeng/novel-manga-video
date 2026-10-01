"""The Agent executor shares production controls without calling models or video services."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.configuration import config_for_novel, model_resources_environment
from novel_manga.application.production import conductor_common, conductor_dispatch, conductor_flow, conductor_state, conductor_workers, control, episode_agents
from novel_manga.application.production.episode_agents import _model_environment as select_agent_model


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def pipeline(root):
    return {'resources': {'video_keys': {'h3': {
        'model': 'minimax-h3-ref2va-turbo', 'base_url': 'pool', 'key_var': '', 'pool': 'test-h3',
        'clip_cap': 15, 'parallel': 3, 'inflight': {'min': 1, 'max': 4, 'start': 2}}},
        'planning_models': {'shared': {'base': 'http://first/v1,http://second/v1', 'model': 'agent-model', 'slots': 2, 'key_var': 'MODEL_KEY'}}},
        'defaults': {'planning': {'block_size': 3, 'margin': 1}},
        'novels': [{'id': 'book', 'title': 'Book', 'novel_dir': str(root / 'book'), 'tmp_dir': str(root / 'production'),
                    'render_keys': ['h3'], 'planning': {'shared': 1}, 'blocks_max': 1, 'blocks_min': 1,
                    'ranges': [{'chapters': '1-3', 'plan_mode': 30}], 'executor': 'episode_agent',
                    'agent': {'input_mode': 'source', 'output_root': str(root / 'agent-output'),
                              'parallel': 2, 'workers': 3}}]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    (tmp_path / 'book').mkdir()
    write(tmp_path / 'book/novel.json', {'novel_id': 'book'})
    config = pipeline(tmp_path)
    write(tmp_path / 'configs/pipeline.json', config)
    monkeypatch.setattr(conductor_common, 'REPO', tmp_path)
    monkeypatch.setattr(control, 'processes', lambda: [])
    monkeypatch.setattr(episode_agents, '_model_environment', lambda c: {'NOVEL_AGENT_MODEL': 'agent-model'})
    spawned = []
    def spawn(c, name, command, extra=None):
        spawned.append((name, command, extra))
        c.procs[name] = SimpleNamespace(pid=900 + len(spawned), poll=lambda: None)
    monkeypatch.setattr(conductor_workers, 'spawn', spawn)
    return tmp_path, config, spawned


def make(config, root):
    return conductor_flow.Conductor(config_for_novel(config, 'book', root=root), dry_run=False)


def test_agent_tick_dispatches_each_episode_only_and_inherits_caps(setup, monkeypatch):
    root, config, spawned = setup
    for name in ('tick_lanes', 'tick_planning', 'tick_review'):
        monkeypatch.setattr(conductor_dispatch, name, lambda *a: pytest.fail('standard dispatch called for Agent episodes'))
    monkeypatch.setattr(conductor_state, 'qwen_waiting', lambda *a: pytest.fail('Agent host does not poll model metrics'))
    c = make(config, root)
    assert c.tick() is True
    assert [row[0] for row in spawned] == ['agent_1', 'agent_2']
    command, env = spawned[0][1:]
    assert command[command.index('--output-root') + 1] == str(root / 'agent-output')
    assert command[command.index('--input-mode') + 1] == 'source'
    assert command[command.index('--episode') + 1] == '1'
    assert env['NOVEL_RENDER_INFLIGHT'] == '4' and env['NOVEL_RENDER_WORKERS'] == '3'
    assert env['NOVEL_AGENT_PAUSE_FILE'] == str(root / 'production/pause')
    assert env['NOVEL_INFLIGHT_POOL'] == 'test-h3'
    c.tick()
    assert len(spawned) == 2
    state = control.read(root / 'production/state.json')
    assert state['executor'] == 'episode_agent' and state['agents']['1']['status'] == 'working'


@pytest.mark.parametrize('terminal', ['finished', 'finished_with_issues', 'blocked'])
def test_saved_terminal_results_never_automatically_rerun(setup, terminal):
    root, config, spawned = setup
    for n in (1, 2, 3):
        write(root / f'agent-output/book/book_{n}-source/agent-run.json',
              {'status': terminal, 'phase': terminal, 'passed': terminal == 'finished', 'reason': 'saved outcome'})
    c = make(config, root)
    assert c.tick() is False
    assert spawned == []
    assert all(row['status'] == terminal for row in c.agents.values())
    assert make(config, root).tick() is False
    assert spawned == []


def test_controller_restarts_adopt_live_external_episode_workers(setup, monkeypatch):
    root, config, spawned = setup
    process = {'pid': 321, 'cwd': str(root), 'args': ['python', 'scripts/episode_agent.py', 'run',
               '--novel-dir', str(root / 'book'), '--episode', '1',
               '--output-root', str(root / 'agent-output'), '--input-mode', 'source']}
    monkeypatch.setattr(control, 'processes', lambda: [process])
    monkeypatch.setattr(control, 'alive', lambda pid: pid == 321)
    write(root / 'agent-output/book/book_1-source/agent-run.json',
          {'phase': 'waiting_resource', 'pid': 321, 'reason': 'waiting for model slot'})
    c = make(config, root)
    c.tick()
    assert c.agents['1']['pid'] == 321
    assert c.agents['1']['phase'] == 'waiting_resource'
    assert [row[0] for row in spawned] == ['agent_2']


def test_external_absolute_episode_and_output_root_are_adopted(setup, monkeypatch):
    root, config, spawned = setup
    external = root / 'external-output'
    process = {'pid': 321, 'cwd': str(root), 'args': ['python', 'scripts/episode_agent.py', 'run',
               '--novel-dir', str(root / 'book'), '--episode', str(root / 'book/book_1'),
               '--output-root', str(external), '--input-mode', 'source']}
    monkeypatch.setattr(control, 'processes', lambda: [process])
    monkeypatch.setattr(control, 'alive', lambda pid: pid == 321)
    write(external / 'book/book_1-source/agent-run.json',
          {'pid': 321, 'phase': 'tool_running', 'waiting_for': 'render'})
    c = make(config, root)
    c.tick()
    assert c.agents['1']['pid'] == 321
    assert c.agents['1']['phase'] == 'tool_running'
    assert c.agents['1']['run_dir'] == str(external / 'book/book_1-source')
    assert [row[0] for row in spawned] == ['agent_2']


def test_external_batch_reserves_future_chapters_and_counts_one_host(setup, monkeypatch):
    root, config, spawned = setup
    config['novels'][0]['ranges'][0]['chapters'] = '1-4'
    external = root / 'external-output'
    process = {'pid': 321, 'cwd': str(root), 'args': ['python', 'scripts/episode_agent.py', 'run',
               '--novel-dir', str(root / 'book'), '--chapters', '1-3',
               '--output-root', str(external), '--input-mode', 'source']}
    monkeypatch.setattr(control, 'processes', lambda: [process])
    monkeypatch.setattr(control, 'alive', lambda pid: pid == 321)
    (root / 'book/book_1').mkdir()
    (root / 'book/book_1/.render.lock').write_text('321')
    c = make(config, root)
    c.tick()
    assert c.agents['1']['status'] == 'working'
    assert [c.agents[str(n)]['phase'] for n in (2, 3)] == ['waiting_external', 'waiting_external']
    assert {c.agents[str(n)]['pid'] for n in (1, 2, 3)} == {321}
    assert [row[0] for row in spawned] == ['agent_4']
    state = control.read(root / 'production/state.json')
    assert len(set(state['workers'].values())) == 2
    # The same batch later reaches chapter 2; chapter 1 retains its actual result.
    (root / 'book/book_1/.render.lock').unlink()
    write(external / 'book/book_1-source/agent-run.json',
          {'pid': 321, 'status': 'finished', 'phase': 'finished', 'passed': True})
    write(external / 'book/book_2-source/agent-run.json',
          {'pid': 321, 'status': 'running', 'phase': 'agent_running'})
    c.tick()
    assert c.agents['1']['status'] == 'finished'
    assert c.agents['2']['phase'] == 'agent_running'
    assert c.agents['3']['phase'] == 'waiting_external'
    assert len(spawned) == 1


def test_external_batch_unstarted_chapters_are_dispatchable_after_exit(setup, monkeypatch):
    root, config, spawned = setup
    process = {'pid': 321, 'cwd': str(root), 'args': ['python', 'scripts/episode_agent.py', 'run',
               '--novel-dir', str(root / 'book'), '--chapters', '1,3',
               '--output-root', str(root / 'external-output'), '--input-mode', 'source']}
    live = [process]
    monkeypatch.setattr(control, 'processes', lambda: live)
    monkeypatch.setattr(control, 'alive', lambda pid: pid == 321)
    c = make(config, root)
    c.tick()
    assert c.agents['1']['phase'] == c.agents['3']['phase'] == 'waiting_external'
    assert [row[0] for row in spawned] == ['agent_2']
    live.clear()
    c.tick()
    assert c.agents['1']['status'] == 'working'
    assert [row[0] for row in spawned] == ['agent_2', 'agent_1']
    assert c.agents['3']['status'] == 'pending'


def test_external_script_context_and_run_directory_survive_controller_restart(setup, monkeypatch):
    root, config, spawned = setup
    external = root / 'external-output'
    runtime = root / 'runtime-project'
    write(root / 'book/book_1/chapter_script.json', {'shots': []})
    process = {'pid': 321, 'cwd': str(root), 'args': ['python', 'scripts/episode_agent.py', 'run',
               '--novel-dir', str(root / 'book'), '--episode', '1', '--project-root', str(runtime),
               '--output-root', str(external), '--input-mode', 'script']}
    live = [process]
    monkeypatch.setattr(control, 'processes', lambda: live)
    monkeypatch.setattr(control, 'alive', lambda pid: pid == 321)
    c = make(config, root)
    c.tick()
    adopted = str(external / 'book/book_1-script')
    assert c.agents['1']['run_dir'] == adopted
    live.clear()
    write(external / 'book/book_1-script/agent-run.json', {'status': 'paused', 'phase': 'paused'})
    restarted = make(config, root)
    restarted.tick()
    assert restarted.agents['1']['run_dir'] == adopted
    command = next(command for name, command, _ in spawned if name == 'agent_1')
    assert command[command.index('--output-root') + 1] == str(external)
    assert command[command.index('--project-root') + 1] == str(runtime)
    assert command[command.index('--input-mode') + 1] == 'script'


def test_external_cli_defaults_resolve_auto_mode_and_project_output(setup, monkeypatch):
    root, config, spawned = setup
    runtime = root / 'runtime-project'
    write(root / 'book/book_1/chapter_script.json', {'shots': []})
    process = {'pid': 321, 'cwd': str(root), 'args': ['python', 'scripts/episode_agent.py', 'run',
               '--novel-dir', str(root / 'book'), '--episode', '1', '--project-root', str(runtime)]}
    monkeypatch.setattr(control, 'processes', lambda: [process])
    monkeypatch.setattr(control, 'alive', lambda pid: pid == 321)
    c = make(config, root)
    c.tick()
    assert c.agents['1']['input_mode'] == 'script'
    assert c.agents['1']['run_dir'] == str(runtime / 'outputs/episode-agent/book/book_1-script')
    assert [row[0] for row in spawned] == ['agent_2']


def test_failed_job_is_visible_in_terminal_snapshot_without_replaying_it(setup):
    root, config, spawned = setup
    job = {'action': 'apply', 'status': 'failed', 'error': 'candidate check failed'}
    write(root / 'agent-output/book/book_1-source/agent-run.json',
          {'status': 'blocked', 'phase': 'blocked', 'reason': 'read the actual plan',
           'session_id': 'saved-session', 'job': job})
    c = make(config, root)
    c.tick()
    assert c.agents['1']['job'] == job and c.agents['1']['session_id'] == 'saved-session'
    assert 'agent_1' not in [row[0] for row in spawned]


def test_pause_drains_hosts_and_resume_keeps_run_directory(setup):
    root, config, spawned = setup
    c = make(config, root)
    c.tick()
    c.agent_pause.write_text('pause')
    c.tick()
    assert len(spawned) == 2
    assert control.read(root / 'production/state.json')['status'] == 'pausing'
    for n in (1, 2):
        c.procs[f'agent_{n}'].poll = lambda: 0
        write(Path(c.agents[str(n)]['run_dir']) / 'agent-run.json', {'status': 'paused', 'phase': 'paused'})
    assert c.tick() is False
    assert control.read(root / 'production/state.json')['status'] == 'paused'
    original = c.agents['1']['run_dir']
    c.agent_pause.unlink()
    resumed = make(config, root)
    resumed.tick()
    assert resumed.agents['1']['run_dir'] == original
    assert len(spawned) == 4


def test_crashed_host_is_blocked_without_retrying_generation(setup):
    root, config, spawned = setup
    c = make(config, root)
    c.tick()
    c.procs['agent_1'].poll = lambda: 1
    c.tick()
    assert c.agents['1']['status'] == 'blocked'
    assert 'without a final' in c.agents['1']['reason']
    assert [row[0] for row in spawned].count('agent_1') == 1


def test_sd_resource_is_rejected_before_agent_dispatch(setup):
    root, config, spawned = setup
    config['resources']['video_keys']['h3']['model'] = 'sd2.5'
    with pytest.raises(ValueError, match='SD'):
        make(config, root)
    assert spawned == []


def test_shared_models_use_global_group_capacity_not_book_allocation(tmp_path, monkeypatch):
    config = pipeline(tmp_path)
    monkeypatch.setenv('NOVEL_TMP_DIR', str(tmp_path / 'tmp'))
    runtime = config_for_novel(config, 'book', root=tmp_path)
    env = runtime['model_environment']
    assert json.loads(env['NOVEL_MODEL_RESOURCES']) == [{'base': 'http://first/v1,http://second/v1', 'slots': 2}]
    assert runtime['planning']['models'][0]['slots'] == 1
    assert env['NOVEL_MODEL_INFLIGHT_DIR'] == str(tmp_path / 'tmp/model-inflight')


def test_standard_executor_is_default_and_override_is_explicit(tmp_path):
    config = pipeline(tmp_path)
    del config['novels'][0]['executor']
    assert config_for_novel(config, 'book', root=tmp_path)['executor'] == 'standard'
    assert config_for_novel(config, 'book', root=tmp_path, executor='episode_agent')['executor'] == 'episode_agent'


def test_agent_status_uses_saved_phase_and_pause_without_mutation(setup, monkeypatch):
    root, config, _ = setup
    spec = config['novels'][0]
    saved = {'executor': 'episode_agent', 'status': 'running', 'workers': {'agent_1': 321},
             'agents': {'1': {'pid': 321, 'status': 'working', 'phase': 'tool_running', 'reason': 'render pending'}},
             'work': {'pending': 2, 'blocked': 0}}
    path = root / 'production/state.json'
    write(path, saved)
    before = path.read_bytes()
    monkeypatch.setattr(control, 'alive', lambda pid: pid == 321)
    report = control.flow_snapshot(root, spec, rows=[] )['production']
    assert report['in_flight'] == 1 and report['executor'] == 'episode_agent'
    assert report['agents']['1']['phase'] == 'tool_running'
    (root / 'production/pause').write_text('paused')
    assert control.flow_snapshot(root, spec, rows=[] )['production']['status'] == 'pausing'
    assert path.read_bytes() == before


def test_agent_executor_is_visible_before_first_saved_tick(setup):
    root, config, _ = setup
    result = control.flow_snapshot(root, config['novels'][0], rows=[])['production']
    assert result['executor'] == 'episode_agent' and result['status'] == 'not_started'


def test_production_stop_and_start_use_pause_without_killing_agent_hosts(setup, monkeypatch):
    root, config, _ = setup
    spec = config['novels'][0]
    owner = {'pid': 12, 'args': ['python', str(root / 'scripts/conductor_thin.py'),
             '--novel', 'book', '--executor', 'episode_agent'], 'cwd': str(root)}
    monkeypatch.setattr(control, 'processes', lambda: [owner])
    monkeypatch.setattr(control.os, 'kill', lambda *a: pytest.fail('Agent stop must drain, not kill'))
    assert control.control(root, spec, 'production', 'stop')['action'] == 'stop_requested'
    assert (root / 'production/pause').is_file()
    assert control.control(root, spec, 'production', 'start')['action'] == 'resumed'
    assert not (root / 'production/pause').exists()


def test_executor_override_reaches_controller_command(setup, monkeypatch):
    root, config, _ = setup
    spec = {**config['novels'][0], 'executor': 'standard'}
    calls = []
    monkeypatch.setattr(control, 'spawn', lambda root, command, log: calls.append(command) or 321)
    result = control.control(root, spec, 'production', 'start', executor='episode_agent')
    assert result['action'] == 'started'
    assert calls[0][-2:] == ['--executor', 'episode_agent']


def test_paused_agent_executor_override_survives_controller_restart(setup, monkeypatch):
    root, config, _ = setup
    spec = {**config['novels'][0], 'executor': 'standard'}
    write(root / 'production/state.json', {'executor': 'episode_agent', 'status': 'paused', 'workers': {}, 'agents': {}})
    (root / 'production/pause').write_text('paused')
    calls = []
    monkeypatch.setattr(control, 'spawn', lambda root, command, log: calls.append(command) or 321)
    control.control(root, spec, 'production', 'start')
    assert calls[0][-2:] == ['--executor', 'episode_agent']
    assert not (root / 'production/pause').exists()


def test_standard_override_cannot_switch_executor_while_agent_controller_runs(setup, monkeypatch):
    root, config, _ = setup
    owner = {'pid': 12, 'args': ['python', str(root / 'scripts/conductor_thin.py'),
             '--novel', 'book', '--executor', 'episode_agent'], 'cwd': str(root)}
    monkeypatch.setattr(control, 'processes', lambda: [owner])
    with pytest.raises(ValueError, match='cannot change'):
        control.control(root, config['novels'][0], 'production', 'start', executor='standard')


def test_pipeline_cli_executor_option_is_forwarded(setup, monkeypatch, capsys):
    import sys
    from novel_manga.application.production import cli
    root, config, _ = setup
    config['novels'][0]['active'] = True
    # Existing range validation requires an eligible key for this fixture.
    config['novels'][0]['ranges'][0]['plan_mode'] = 15
    monkeypatch.setattr(cli, 'ROOT', root)
    monkeypatch.setattr(cli, 'load', lambda: config)
    monkeypatch.setattr(sys, 'argv', ['pipeline.py', 'start', '--novel', 'book', '--executor', 'episode_agent'])
    calls = []
    monkeypatch.setattr(cli, 'control', lambda *args, **kw: calls.append(kw) or {'action': 'started'})
    assert cli.main() == 0
    assert calls[0]['executor'] == 'episode_agent'
    capsys.readouterr()


def test_model_override_uses_only_matching_allocated_server(setup, monkeypatch):
    from novel_manga.application.agents import sandbox
    root, config, _ = setup
    c = make(config, root)
    monkeypatch.setattr(sandbox, 'load_config', lambda path: {'model': 'agent-model', 'key_var': 'MODEL_KEY'})
    matched = select_agent_model(c)
    assert matched['NOVEL_AGENT_MODEL'] == 'agent-model'
    assert matched['NOVEL_AGENT_BASE_URL'] == 'http://first'
    assert matched['QWEN38_LOCAL_BASE_URL'] == 'http://first/v1,http://second/v1'
    c.cfg['planning']['models'][0]['endpoint'] = 'flashnext'
    monkeypatch.setattr(sandbox, 'load_config', lambda path: {'model': 'agent-model', 'key_var': 'MODEL_KEY',
                                                            'base_url': 'http://second'})
    matched = select_agent_model(c)
    assert matched['NOVEL_AGENT_BASE_URL'] == 'http://second'
    assert matched['QWEN38_LOCAL_BASE_URL'] == 'http://first/v1,http://second/v1'
    assert matched['SECOND_REVIEW_JUDGE'] == matched['NOVEL_REVIEW_VIDEO_JUDGE'] == 'flashnext'
    monkeypatch.setattr(sandbox, 'load_config', lambda path: {'model': 'different-model', 'key_var': 'OTHER_KEY'})
    assert select_agent_model(c) == {}


def test_agent_aimd_reads_actual_host_log_and_publishes_shared_limit(setup):
    from datetime import datetime
    import time
    root, config, spawned = setup
    config['resources']['video_keys']['h3']['inflight']['start'] = 4
    config['defaults']['aimd'] = {'decrease_at': 1, 'window_seconds': 60}
    c = make(config, root)
    c.lanes['h3']['limit_changed'] = time.time() - 60
    (root / 'production/agent_1.log').write_text(f'[{datetime.now():%H:%M:%S}] HTTP 429: H3 request throttled\n')
    c.tick()
    assert c.lanes['h3']['limit'] == 2
    pool = root / 'book/.inflight-test-h3'
    assert (pool / 'limit').read_text() == '2'
    assert spawned[0][2]['NOVEL_INFLIGHT_DIR'] == str(pool)
    assert control.read(root / 'production/state.json')['pools']['h3']['limit'] == 2


def test_parked_h3_stops_new_hosts_while_existing_hosts_drain(setup):
    from datetime import datetime
    root, config, spawned = setup
    config['novels'][0]['agent']['parallel'] = 1
    c = make(config, root)
    c.tick()
    assert len(spawned) == 1
    (root / 'production/agent_1.log').write_text(f'[{datetime.now():%H:%M:%S}] quota_not_enough\n')
    c.agent_settings['parallel'] = 2
    c.tick()
    assert len(spawned) == 1
    assert c.agents['1']['status'] == 'working'
    assert c.agents['2']['waiting_for'] == 'video'
    assert c.agents['2']['phase'] == 'waiting_resource'


def test_model_wait_and_agent_time_are_visible_to_shared_status(setup, monkeypatch):
    root, config, _ = setup
    process = {'pid': 321, 'cwd': str(root), 'args': ['python', 'episode_agent.py', 'run',
               '--novel-dir', str(root / 'book'), '--episode', '1',
               '--output-root', str(root / 'agent-output'), '--input-mode', 'source']}
    monkeypatch.setattr(control, 'processes', lambda: [process])
    monkeypatch.setattr(control, 'alive', lambda pid: pid == 321)
    write(root / 'agent-output/book/book_1-source/agent-run.json',
          {'phase': 'waiting_resource', 'waiting_for': 'agent_model', 'agent_seconds': 2.5})
    c = make(config, root)
    c.tick()
    result = control.flow_snapshot(root, config['novels'][0])['production']
    assert result['agents']['1']['waiting_for'] == 'agent_model'
    assert result['agents']['1']['agent_seconds'] == 2.5


def test_spawn_passes_model_resource_environment_to_standard_workers(tmp_path, monkeypatch):
    model_env = model_resources_environment(tmp_path, pipeline(tmp_path))
    c = SimpleNamespace(dry=False, cfg={'model_environment': model_env}, tmp=tmp_path, procs={}, log=lambda text: None)
    captured = {}
    monkeypatch.setattr(conductor_workers.subprocess, 'Popen', lambda *a, **kw: captured.update(kw) or SimpleNamespace(pid=100))
    conductor_workers.spawn(c, 'review', ['fake-review'])
    assert captured['env']['NOVEL_MODEL_RESOURCES'] == model_env['NOVEL_MODEL_RESOURCES']
    assert captured['env']['NOVEL_MODEL_INFLIGHT_DIR'] == model_env['NOVEL_MODEL_INFLIGHT_DIR']
