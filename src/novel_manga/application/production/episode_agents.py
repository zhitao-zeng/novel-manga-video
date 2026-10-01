"""Dispatch isolated episode hosts through the existing Conductor process list."""
from __future__ import annotations

from pathlib import Path
import os
import time

from novel_manga.application.configuration import episode_agent_settings, validate_agent_h3
from novel_manga.application.agents.episode.context import create_context
from novel_manga.util import atomic_write_json, read_json
from . import conductor_capacity, conductor_common, conductor_workers
from .common import parse_chapters

TERMINAL = {'finished', 'finished_with_issues', 'blocked'}


def configure(conductor):
    conductor.agent_settings = episode_agent_settings(conductor_common.REPO, {'agent': conductor.cfg.get('agent', {})})
    validate_agent_h3(conductor.keys.values())
    saved = read_json(conductor.tmp / 'state.json', {})
    previous = saved.get('agents', {}) if saved.get('executor') == 'episode_agent' else {}
    conductor.agents = {}
    for n in sorted({n for r in conductor.ranges for n in range(r['a'], r['b'] + 1)}):
        row = dict(previous.get(str(n), {}))
        row.update(episode=n, status=row.get('status', 'pending'), phase=row.get('phase', 'ready_agent'))
        row.setdefault('key', list(conductor.keys)[(n - 1) % len(conductor.keys)])
        mode = row.get('input_mode', conductor.agent_settings['input_mode'])
        try:
            output = row.get('output_root', conductor.agent_settings['output_root'])
            ctx = create_context(conductor.novel_dir, n, output_root=output, input_mode=mode)
            row.setdefault('run_dir', str(ctx.run_dir))
            row.setdefault('output_root', output)
            row.update(input_mode=ctx.input_mode,
                       source_episode=str(ctx.source_episode_dir))
        except ValueError as error:
            row.update(status='blocked', phase='blocked', reason=str(error))
        conductor.agents[str(n)] = row
    conductor.agent_pause = conductor.tmp / 'pause'


def _worker(conductor, row, processes):
    from .control import alive, argument
    name = f"agent_{row['episode']}"
    proc = conductor.procs.get(name)
    if proc is not None and proc.poll() is None:
        return proc.pid, False
    for process in processes:
        args = process['args']
        script = next((i for i, arg in enumerate(args) if Path(arg).name == 'episode_agent.py'), None)
        if script is None or script + 1 >= len(args) or args[script + 1] != 'run':
            continue
        directory = argument(args, '--novel-dir')
        episode = argument(args, '--episode')
        chapters = argument(args, '--chapters')
        if not directory:
            continue
        cwd = Path(process.get('cwd', conductor_common.REPO))
        if (cwd / directory).resolve() != conductor.novel_dir or not alive(process['pid']):
            continue
        if chapters:
            if row['episode'] not in parse_chapters(chapters):
                continue
            selected = row['episode']
        elif episode:
            selected = episode
            if episode.isdigit():
                if int(episode) != row['episode']:
                    continue
            else:
                candidate = Path(episode)
                source = candidate.resolve() if candidate.is_absolute() else conductor.novel_dir / candidate
                if source != Path(row.get('source_episode') or conductor.episode_dir(row['episode'])):
                    continue
        else:
            continue
        project = argument(args, '--project-root')
        project = (cwd / project).resolve() if project else (cwd / args[script]).resolve().parents[1]
        output = argument(args, '--output-root')
        output = (cwd / output).resolve() if output else project / 'outputs/episode-agent'
        mode = argument(args, '--input-mode', 'auto')
        row.update(output_root=str(output), project_root=str(project))
        try:
            ctx = create_context(conductor.novel_dir, selected, output_root=output, input_mode=mode)
            row.update(run_dir=str(ctx.run_dir), input_mode=ctx.input_mode,
                       source_episode=str(ctx.source_episode_dir))
            row.pop('external_context_error', None)
        except ValueError as error:
            # Reserve a declared batch chapter even if its CLI will reject the input.
            row.update(run_dir='', input_mode=mode, external_context_error=str(error))
        return process['pid'], bool(chapters)
    return None


def _observe(conductor, row, processes):
    worker = _worker(conductor, row, processes)
    pid, batch = worker if worker is not None else (None, False)
    result = read_json(Path(row['run_dir']) / 'agent-run.json', {}) if row.get('run_dir') else {}
    execution = read_json(Path(row['run_dir']) / 'execution.json', {}) if row.get('run_dir') else {}
    for field in ('job', 'session_id', 'attempts', 'deadline', 'waiting_for', 'agent_seconds'):
        if field in result:
            row[field] = result[field]
    completed_batch = bool(pid and batch and result.get('pid') == pid and result.get('status') in TERMINAL)
    if pid and not completed_batch:
        if batch:
            try:
                owner = int((Path(row['source_episode']) / '.render.lock').read_text().strip())
            except (OSError, ValueError, KeyError):
                owner = None
            current = owner == pid or (result.get('pid') == pid and result.get('status') not in TERMINAL | {'paused'})
            if not current:
                row.update(pid=pid, status='waiting_external', phase='waiting_external', waiting_for='external_batch',
                           reason='外部批任务待轮到该集')
                return
        phase = result.get('phase') or execution.get('phase') or 'agent_running'
        row.update(pid=pid, status='working', phase=phase,
                   reason=result.get('reason') or execution.get('reason') or '')
        return
    row['pid'] = pid if completed_batch else None
    if row.get('external_context_error'):
        row.update(status='blocked', phase='blocked', reason=row['external_context_error'])
        return
    if result.get('status') in TERMINAL | {'paused'}:
        reason = result.get('reason') or ''
        if result['status'] == 'finished_with_issues' and not reason:
            quality = (result.get('finish') or {}).get('quality_review') or {}
            reason = ('unresolved clips: ' + ', '.join(quality['remaining']) if quality.get('remaining')
                      else quality.get('reason') or 'episode finished with unresolved checks')
        row.update(status=result['status'], phase=result.get('phase') or result['status'],
                   reason=reason, passed=result.get('passed') is True)
        return
    if row.get('status') == 'working':
        row.update(status='blocked', phase='blocked', reason='episode worker exited without a final or paused result')
    elif row.get('status') == 'waiting_external':
        row.update(status='pending', phase='ready_agent', reason='', waiting_for=None)


def _model_environment(conductor):
    """Override the Agent only with an allocated server matching its configured model."""
    from novel_manga.application.agents.sandbox import load_config
    path = conductor_common.REPO / 'configs/agent_sandbox.json'
    config = load_config(path)
    matches = [server for server in conductor.cfg.get('planning', {}).get('models', [])
               if server.get('model') == config['model']]
    if not matches:
        return {}
    server = matches[0]
    bases = [base.strip().rstrip('/') for base in server['base'].split(',') if base.strip()]
    configured = str(config.get('base_url') or '').rstrip('/').removesuffix('/v1')
    selected = next((base for base in bases if base.removesuffix('/v1') == configured), bases[0])
    return {**conductor_workers.server_env(conductor, server),
            'NOVEL_AGENT_BASE_URL': selected.removesuffix('/v1'), 'NOVEL_AGENT_MODEL': server['model'],
            'NOVEL_AGENT_KEY_VAR': server.get('key_var') or config['key_var'],
            **({'SECOND_REVIEW_JUDGE': server['endpoint'], 'NOVEL_REVIEW_VIDEO_JUDGE': server['endpoint']}
               if server.get('endpoint') else {})}


def tick(conductor):
    from .control import processes
    rows = processes()
    for row in conductor.agents.values():
        _observe(conductor, row, rows)
    for name, key in conductor.keys.items():
        chapters = [row['episode'] for row in conductor.agents.values() if row.get('key') == name]
        conductor_capacity.tick_aimd(conductor, name, key, chapters)
    paused = conductor.agent_pause.is_file()
    active = [row for row in conductor.agents.values() if row.get('pid')]
    available = max(0, conductor.agent_settings['parallel'] - len({row['pid'] for row in active}))
    if not paused and available:
        for row in conductor.agents.values():
            if not available:
                break
            if row.get('pid') or row['status'] in TERMINAL:
                continue
            if row['key'] not in conductor.keys:
                row.update(status='blocked', phase='blocked', reason='assigned video resource is no longer configured')
                continue
            key = conductor.keys[row['key']]
            if conductor.lanes[row['key']]['parked_until'] > time.time():
                row.update(status='waiting_resource', phase='waiting_resource', waiting_for='video',
                           reason='assigned H3 resource is parked')
                continue
            options = conductor.agent_settings
            command = [conductor_common.PY, str(conductor_common.SCRIPTS / 'episode_agent.py'), 'run',
                       '--novel-dir', str(conductor.novel_dir), '--episode', str(row['episode']),
                       '--project-root', row.get('project_root', str(conductor_common.REPO)),
                       '--output-root', row.get('output_root', options['output_root']),
                       '--input-mode', row['input_mode']]
            if options.get('timeout'):
                command += ['--timeout', str(options['timeout'])]
            extra = {**_model_environment(conductor), **conductor_workers.key_env(conductor, key),
                     'NOVEL_AGENT_MANAGED': '1',
                     'NOVEL_INFLIGHT_DIR': str(conductor_capacity.pool_dir(conductor, key)),
                     'NOVEL_AGENT_PAUSE_FILE': str(conductor.agent_pause),
                     'NOVEL_RENDER_INFLIGHT': str(key['inflight']['max']),
                     'NOVEL_RENDER_WORKERS': str(options['workers'])}
            conductor_workers.spawn(conductor, f"agent_{row['episode']}", command, extra)
            proc = conductor.procs.get(f"agent_{row['episode']}")
            if not conductor.dry and proc is not None:
                row.update(status='working', phase='ready_agent', pid=proc.pid, reason='', waiting_for=None)
            available -= 1
    active = [row for row in conductor.agents.values() if row.get('pid')]
    pending = [row for row in conductor.agents.values() if row['status'] not in TERMINAL]
    blocked = [row for row in conductor.agents.values() if row['status'] in {'blocked', 'finished_with_issues'}]
    status = ('pausing' if active else 'paused') if paused else (
        'running' if pending else 'blocked' if blocked else 'complete')
    state = {'executor': 'episode_agent', 'agents': conductor.agents, 'status': status,
             'lanes': conductor.lanes,
             'pools': {name: {'held': conductor_capacity.held_slots(conductor, key),
                              'limit': conductor.lanes[name]['limit'],
                              'parked_until': conductor.lanes[name]['parked_until']}
                       for name, key in conductor.keys.items()},
             'pid': os.getpid(), 'time': time.time(),
             'workers': {f"agent_{row['episode']}": row['pid'] for row in active},
             'work': {'pending': len(pending), 'blocked': len(blocked)}}
    if not conductor.dry:
        atomic_write_json(conductor.tmp / 'state.json', state)
    conductor.log(f"episode agents: {len({row['pid'] for row in active})} hosts, {len(pending)} pending, {len(blocked)} blocked; {status}")
    return bool(pending) and not (paused and not active)
