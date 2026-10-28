"""Dispatch isolated episode hosts through the existing Conductor process list."""
from __future__ import annotations

from pathlib import Path
import os
import time

from novel_manga.application.configuration import episode_agent_settings, validate_agent_h3
from novel_manga.application.agents.episode.context import create_context
from novel_manga.util import atomic_write_json, read_json
from . import conductor_capacity, conductor_common, conductor_workers, agent_preparation, agent_models
from .common import parse_chapters

TERMINAL = {'finished', 'finished_with_issues', 'blocked'}


def configure(conductor):
    conductor.agent_settings = episode_agent_settings(conductor_common.REPO, {'agent': conductor.cfg.get('agent', {})})
    validate_agent_h3(conductor.keys.values())
    saved = read_json(conductor.tmp / 'state.json', {})
    conductor.task_pool_episodes=(saved.get('task_pool') or {}).get('episodes',[])
    previous = saved.get('agents', {}) if saved.get('executor') == 'episode_agent' else {}
    conductor.agents = {}
    for n in sorted({n for r in conductor.ranges for n in range(r['a'], r['b'] + 1)}):
        row = dict(previous.get(str(n), {}))
        row.update(episode=n, status=row.get('status', 'pending'), phase=row.get('phase', 'ready_agent'))
        assigned = next((r.get('render_keys') for r in conductor.ranges if r['a'] <= n <= r['b']), None)
        keys = [name for name in conductor.keys if not assigned or name in assigned]
        if not keys:
            raise ValueError(f'chapter {n} has no assigned render resource')
        row.setdefault('key', keys[(n - 1) % len(keys)])
        mode = row.get('input_mode', conductor.agent_settings['input_mode'])
        started = bool(row.get('run_dir') and any((Path(row['run_dir'])/name).exists()
            for name in ('agent-run.json', 'prepared.json', 'execution.json')))
        if conductor.agent_settings['input_mode'] == 'auto' and not started:
            mode = 'auto'
            row['requested_input_mode'] = mode
        else:
            row.setdefault('requested_input_mode', mode)
        try:
            output = row.get('output_root', conductor.agent_settings['output_root'])
            ctx = create_context(conductor.novel_dir, n, output_root=output, input_mode=mode)
            if mode == 'auto' and not started:
                row['run_dir'] = str(ctx.run_dir)
            else:
                row.setdefault('run_dir', str(ctx.run_dir))
            row.setdefault('output_root', output)
            row.update(input_mode=ctx.input_mode,
                       source_episode=str(ctx.source_episode_dir))
        except ValueError as error:
            row.update(status='blocked', phase='blocked', reason=str(error))
        conductor.agents[str(n)] = row
    conductor.agent_pause = conductor.tmp / 'pause'
    agent_models.configure(conductor)
    if agent_preparation.independent(conductor) and (not conductor.agent_model_groups or any(
            group['config'].get('production_mode')!='pipeline_review' for group in conductor.agent_model_groups.values())):
        raise ValueError('independent production capacity requires pipeline_review model configurations')


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
        config = argument(args, '--config')
        agent_models.observe(conductor, row, {}, config_path=(cwd/config).resolve() if config else None)
        if '--retry-issues' in args:
            row['issue_retry_started'] = True
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
    agent_models.observe(conductor, row, result)
    if (row.get('issue_retry_started') or result.get('status') in {'finished_with_issues', 'blocked'}
            or result.get('pipeline_phase') in {'input_repair','review'}):
        row['production_lane'] = 'repair'
    execution = read_json(Path(row['run_dir']) / 'execution.json', {}) if row.get('run_dir') else {}
    for field in ('job', 'session_id', 'attempts', 'deadline', 'waiting_for', 'agent_seconds','pipeline_phase'):
        if field in result:
            row[field] = result[field]
    completed_batch = bool(pid and batch and result.get('pid') == pid and result.get('status') in TERMINAL | {'deferred'})
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
    if result.get('status') in TERMINAL | {'paused', 'deferred'}:
        if not conductor.agent_pause.is_file() and row.get('source_episode'):
            from novel_manga.application.agents.episode.recovery import (
                resume_source_review,resume_protocol,resume_handoff,resume_review)
            from novel_manga.application.agents.episode.input_repair import repair as validate_input
            episode_dir = Path(row['run_dir'])/conductor.novel_dir.name/Path(row['source_episode']).name
            result = resume_source_review(row['run_dir'], episode_dir, result) or result
            result = resume_protocol(row['run_dir'], episode_dir, result,validate_input=validate_input) or result
            result = resume_handoff(row['run_dir'], episode_dir, result) or result
            result = resume_review(row['run_dir'], episode_dir, result) or result
        reason = result.get('reason') or ''
        if result['status'] == 'finished_with_issues' and not reason:
            quality = (result.get('finish') or {}).get('quality_review') or {}
            reason = ('unresolved clips: ' + ', '.join(quality['remaining']) if quality.get('remaining')
                      else quality.get('reason') or 'episode finished with unresolved checks')
        row.update(status=result['status'], phase=result.get('phase') or result['status'],
                   reason=reason, passed=result.get('passed') is True, delivery=result.get('delivery'))
        if row['passed'] and (row.get('delivery') or {}).get('status') == 'pending':
            # Resume the accepted host result, not another repair or generation.
            # agent-run remains finished so the ordinary entrypoint skips Agent work.
            row.update(status='deferred', phase='delivery', waiting_for='upload',
                       reason=row['delivery']['reason'])
        elif row['passed'] and (row.get('delivery') or {}).get('verified_remote'):
            row['waiting_for'] = None
        return
    if row.get('status') == 'working':
        row.update(status='blocked', phase='blocked', reason='episode worker exited without a final or paused result')
    elif row.get('status') == 'waiting_external':
        row.update(status='pending', phase='ready_agent', reason='', waiting_for=None)


def _model_environment(conductor, row=None):
    """Override the Agent only with an allocated server matching its configured model."""
    from novel_manga.application.agents.sandbox import load_config
    options = agent_models.options(conductor, row)
    path = Path(options.get('config') or conductor_common.REPO / 'configs/agent_sandbox.json')
    config = load_config(path)
    matches = [server for server in conductor.cfg.get('planning', {}).get('models', [])
               if server.get('model') == config['model']]
    if not matches:
        return {}
    server = matches[0]
    bases = [base.strip().rstrip('/') for base in server['base'].split(',') if base.strip()]
    configured = str(config.get('base_url') or '').rstrip('/').removesuffix('/v1')
    selected = next((base for base in bases if base.removesuffix('/v1') == configured), bases[0])
    key_var = server.get('key_var') or config['key_var']
    from novel_manga.application.configuration import environment
    credentials = environment(conductor_common.REPO)
    roles = {}
    if endpoint := server.get('endpoint'):
        roles = {name: endpoint for name in ('NOVEL_PREPARATION_ENDPOINT', 'NOVEL_PLANNER_ENDPOINT',
            'SECOND_REVIEW_JUDGE', 'NOVEL_REVIEW_VIDEO_JUDGE', 'VERIFY_JUDGE')}
        roles.update(NOVEL_H3_TRANSLATION_ENDPOINT=config.get('h3_translation_endpoint', endpoint),
                     NOVEL_H3_REQUEST_CHECK_ENDPOINT=config.get('h3_request_check_endpoint', endpoint),
                     NOVEL_REFUSAL_FALLBACK=(config.get('refusal_fallback') or {}).get('endpoint', ''))
    return {**conductor_workers.server_env(conductor, server), **roles,
            'NOVEL_AGENT_BASE_URL': selected.removesuffix('/v1'), 'NOVEL_AGENT_MODEL': server['model'],
            'NOVEL_AGENT_KEY_VAR': key_var,
            **({'NOVEL_LLM_API_KEY': credentials[key_var]} if credentials.get(key_var) else {}),
            **({'SECOND_REVIEW_JUDGE': server['endpoint'], 'NOVEL_REVIEW_VIDEO_JUDGE': server['endpoint']}
               if server.get('endpoint') else {})}


def _summary_retry_due(conductor, row):
    if row.get('context_recovery_started') or row.get('status') != 'blocked' or not row.get('run_dir'):
        return False
    group = conductor.agent_model_groups.get(row.get('model_endpoint'), {})
    fallback = (group.get('config') or {}).get('refusal_fallback') or {}
    if not all(fallback.get(k) for k in ('model', 'base_url', 'key_var')):
        return False
    from novel_manga.application.agents.episode.recovery import blocked_summary_loop
    return blocked_summary_loop(row['run_dir'])


def _retry_due(conductor, row):
    reasons = conductor.agent_settings.get('retry_reasons')
    chapters = conductor.agent_settings.get('retry_chapters')
    selected = ((reasons is None and chapters is None)
                or (reasons is not None and row.get('reason') in reasons)
                or (chapters is not None and row['episode'] in chapters))
    return bool(conductor.agent_settings.get('retry_issues')
                and row.get('preparation_status') != 'needs_source'
                and row['status'] in {'finished_with_issues', 'blocked'}
                and not row.get('passed') and (not row.get('issue_retry_started') or _summary_retry_due(conductor, row))
                and row.get('run_dir') and not row.get('external_context_error')
                and selected
                and row.get('key') in conductor.keys)


def tick(conductor):
    from .control import processes
    rows = processes()
    for row in conductor.agents.values():
        if agent_preparation.observe(conductor, row, rows):
            continue
        _observe(conductor, row, rows)
    from . import batch_review
    review_eligible={row['episode'] for row in conductor.agents.values() if batch_review.observe(conductor,row)}
    for name, key in conductor.keys.items():
        chapters = [row['episode'] for row in conductor.agents.values() if row.get('key') == name]
        conductor_capacity.tick_aimd(conductor, name, key, chapters)
    paused = conductor.agent_pause.is_file()
    active = [row for row in conductor.agents.values() if row.get('pid')]
    candidates = [row for row in conductor.agents.values() if not row.get('pid')
                  and row['episode'] in review_eligible
                  and (row['status'] not in TERMINAL or _retry_due(conductor, row))]
    first_priority = conductor.agent_settings.get('first_pass_priority', False)
    first_supply = first_priority and (any(agent_preparation.normal_work(conductor, r) for r in active)
        or any(agent_preparation.normal_work(conductor, r) and not agent_preparation.assets_waiting(conductor, r)
               for r in candidates))
    candidates=agent_preparation.task_pool(conductor,candidates,active)
    available = agent_preparation.available_hosts(conductor)
    split=agent_preparation.independent(conductor)
    if not paused and getattr(conductor,'agent_dispatch_enabled',True) and any(available.values()):
        # Finish started episodes before opening more. Otherwise first passes
        # keep GPUs busy while accepted delivery waits behind the entire book.
        for row in agent_preparation.dispatch_order(conductor, candidates):
            if not any(available.values()):
                break
            retry = _retry_due(conductor, row)
            if row.get('pid') or (row['status'] in TERMINAL and not retry):
                continue
            if agent_preparation.assets_waiting(conductor,row):continue
            if not batch_review.may_dispatch(conductor,row,candidates):continue
            kind=agent_preparation.lane(conductor,row) if split else 'shared'
            if first_supply and kind == 'repair':
                continue  # Current operations drain; new post-render work waits for first video supply.
            if not available[kind]:continue
            if row['key'] not in conductor.keys:
                row.update(status='blocked', phase='blocked', reason='assigned video resource is no longer configured')
                continue
            key = conductor.keys[row['key']]
            if conductor.lanes[row['key']]['parked_until'] > time.time():
                row.update(status=row['status'] if retry else 'waiting_resource',
                           phase='waiting_resource', waiting_for='video',
                           reason='assigned H3 resource is parked')
                continue
            if not agent_models.assign(conductor, row):
                continue
            options = agent_models.options(conductor, row)
            model_env = _model_environment(conductor, row)
            if not agent_preparation.admit(conductor, row, model_env):
                if row.get('pid'):available[kind] -= 1
                continue
            # Runtime data may live outside this checkout. Keep child hosts on
            # the same code branch as their controller.
            script = Path(__file__).resolve().parents[4] / 'scripts/episode_agent.py'
            command = [conductor_common.PY, str(script), 'run',
                       '--novel-dir', str(conductor.novel_dir), '--episode', str(row['episode']),
                       '--project-root', row.get('project_root', str(conductor_common.REPO)),
                       '--output-root', row.get('output_root', options['output_root']),
                       '--input-mode', row['input_mode']]
            manifest = read_json(Path(row['run_dir']) / 'sandbox/input/manifest.json', {})
            target = manifest.get('target_seconds') or options.get('target_seconds')
            if target:
                durations = target if isinstance(target, (list, tuple)) else [target]
                command += ['--target-seconds', *(str(value) for value in durations)]
            if options.get('config'):
                command += ['--config', options['config']]
            if options.get('timeout'):
                command += ['--timeout', str(options['timeout'])]
            extra = {**model_env, **conductor_workers.key_env(conductor, key),
                     'NOVEL_AGENT_MANAGED': '1',
                     'NOVEL_INFLIGHT_DIR': str(conductor_capacity.pool_dir(conductor, key)),
                     'NOVEL_AGENT_PAUSE_FILE': str(conductor.agent_pause),
                     'NOVEL_RENDER_INFLIGHT': str(key['inflight']['max']),
                     'NOVEL_RENDER_WORKERS': str(options['workers'])}
            if split:
                extra['NOVEL_PRODUCTION_ONLY']='1' if kind=='normal' else '0'
                extra['NOVEL_FIRST_PASS_ONLY']='1' if first_priority and kind=='normal' else '0'
            if conductor.agent_settings.get('queue')=='batch_review':
                first_input=row.get('batch_stage')=='input_repair'
                extra.update(NOVEL_FIRST_PASS_ONLY='1' if first_input else '0',NOVEL_PRODUCTION_ONLY='0',
                             NOVEL_VIDEO_PRIORITY='first' if first_input else 'repair')
            if options.get('asset_manifest'):
                extra['NOVEL_ASSET_PREPARATION_MANIFEST']=options['asset_manifest']
            if retry:
                command.append('--retry-issues')
                if not conductor.dry:
                    # Persist before launch, so restarting the controller can
                    # adopt/resume this repair but cannot grant another one.
                    if _summary_retry_due(conductor, row):
                        row['context_recovery_started'] = True
                    row['issue_retry_started'] = True
                    saved = read_json(conductor.tmp / 'state.json', {})
                    atomic_write_json(conductor.tmp / 'state.json',
                                      {**saved, 'executor': 'episode_agent', 'agents': conductor.agents})
            conductor_workers.spawn(conductor, f"agent_{row['episode']}", command, extra)
            proc = conductor.procs.get(f"agent_{row['episode']}")
            if not conductor.dry and proc is not None:
                row.update(status='working', phase='ready_agent', pid=proc.pid, reason='', waiting_for=None)
            available[kind] -= 1
    active = [row for row in conductor.agents.values() if row.get('pid')]
    pending = [row for row in conductor.agents.values() if row['episode'] in review_eligible
               and (row['status'] not in TERMINAL or _retry_due(conductor, row))]
    blocked = [row for row in conductor.agents.values() if row['status'] in {'blocked', 'finished_with_issues'}]
    status = ('pausing' if active else 'paused') if paused else (
        'running' if pending else 'blocked' if blocked else 'complete')
    state = {'executor': 'episode_agent', 'agents': conductor.agents, 'status': status,
             **({'text_capacity':{'limit':conductor.agent_settings['parallel'],
                 'hosts':sum(batch_review.text_host(conductor,r) for r in active),
                 'background_hosts':sum(batch_review.background(r) for r in active),
                 'models':{name:{'limit':g['slots'],
                    'hosts':sum(batch_review.text_host(conductor,r) and r.get('model_endpoint')==name for r in active)}
                    for name,g in conductor.agent_model_groups.items()}}}
                if conductor.agent_settings.get('queue')=='batch_review' else {}),
             **({'task_pool':{'limit':conductor.agent_settings['task_pool_size'],
                             'episodes':conductor.task_pool_episodes}} if conductor.agent_settings.get('task_pool_size') else {}),
             **({'host_capacity':{'production':conductor.agent_settings['production_parallel'],
                 'repair':conductor.agent_settings['parallel']},
                 'hosts_by_lane':{kind:sum(agent_preparation.lane(conductor,r)==kind for r in active)
                                  for kind in ('normal','repair')}} if split else {}),
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
