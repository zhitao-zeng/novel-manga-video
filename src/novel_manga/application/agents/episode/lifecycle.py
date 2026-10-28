"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations
from dataclasses import dataclass, field
import threading
from . import phase_budget
from .context import EpisodeContext
from . import runtime as runtime_rules
from novel_manga.application.resolved_settings import resolve_agent_environment
from . import progress as progress_rules

from contextlib import contextmanager
import os
import shutil
from pathlib import Path
import time

from novel_manga.util import atomic_write_json, read_json

from .session import SESSION_FILE
from .inputs import prepare_inputs
from .bridge import install_client
from .limits import DEFAULT_OPERATION_SECONDS, DEFAULT_REPAIR_TURN_SECONDS
from novel_manga.application.agents.sandbox import load_config




def wait_for_idle(bridge):
    status = bridge.operation_status()
    next_update = time.monotonic() + 30
    while status.get('busy'):
        if time.monotonic() >= next_update:
            job = status.get('job') or {}
            print(f"Waiting for episode operation {job.get('id')}: {job.get('action')}", flush=True)
            next_update = time.monotonic() + 30
        time.sleep(0.2)
        status = bridge.operation_status()
    return bridge.handle('status', {})


def paused():
    path = os.environ.get('NOVEL_AGENT_PAUSE_FILE')
    return bool(path and Path(path).exists())


@contextmanager
def episode_lock(ctx):
    """Use the production episode marker so a second executor cannot claim it."""
    from novel_manga.application.production.common import pid_alive
    path = ctx.source_episode_dir / '.render.lock'
    ctx.source_episode_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            owner = int(path.read_text().strip() or 0)
        except ValueError:
            owner = 0
        if owner and pid_alive(owner):
            raise RuntimeError(f'episode is already owned by pid {owner}')
        path.unlink(missing_ok=True)
    with path.open('x') as stream:
        stream.write(str(os.getpid()))
    try:
        yield
    finally:
        if path.exists() and path.read_text().strip() == str(os.getpid()):
            path.unlink()


def current_finish(ctx, previous_stamp, job):
    receipt = (job or {}).get('result') or {}
    if ((job or {}).get('action') != 'finish' or (job or {}).get('status') != 'completed'
            or type(receipt.get('passed')) is not bool):
        return None
    path = ctx.work_dir / 'input/latest-finish.json'
    stamp = progress_rules.file_stamp(path)
    if stamp is None or stamp == previous_stamp:
        return None
    manifest = read_json(path, None)
    if not isinstance(manifest, dict):
        return None
    current = read_json(ctx.work_dir / SESSION_FILE, {}).get('session_id')
    if not current or (manifest.get('author_session') or {}).get('session_id') != current:
        return None
    if manifest.get('passed') is not receipt['passed']:
        return None
    return manifest


def record_finish(result, manifest):
    passed = manifest.get('passed') is True
    result.update(status='finished' if passed else 'finished_with_issues', phase='finished',
                  passed=passed, exit_code=0 if passed else 1, finish=manifest, reason=None)


def read_status(ctx):
    """Read a prepared or running episode; never start a model, bridge or media task."""
    return {'episode': ctx.source_episode_dir.name, 'input_mode': ctx.input_mode,
            'run_dir': str(ctx.run_dir), 'execution': read_json(ctx.run_dir / 'execution.json', {}),
            'agent_run': read_json(ctx.run_dir / 'agent-run.json', {}),
            'session': read_json(ctx.work_dir / SESSION_FILE, {}),
            'finish': read_json(ctx.work_dir / 'input/latest-finish.json', None)}


def finish_existing(ctx, saved, runtime, tools_factory):
    """The ordinary host acceptance operation, without authoring or generation."""
    result = {**saved, 'episode': ctx.source_episode_dir.name, 'input_mode': ctx.input_mode,
              'run_dir': str(ctx.run_dir), 'pid': os.getpid(), 'passed': False,
              'status': 'running', 'phase': 'checking_existing_media', 'reason': None,
              'updated_at': time.strftime('%F %T'),
              'method': 'existing media acceptance under current book policy'}
    result.pop('finish', None)
    with runtime_rules.process_environment(runtime):
        if paused():
            result.update(status='paused', phase='paused', exit_code=0, reason='production dispatch paused')
        else:
            atomic_write_json(ctx.run_dir/'agent-run.json', result)
            tools = tools_factory(ctx)
            answer = tools.dispatch('finish', {})
            if answer.get('required_action') == 'review':
                reviewed = tools.dispatch('review', {})
                result['existing_media_review'] = reviewed
                answer = (tools.dispatch('finish', {}) if reviewed.get('ok') is not False
                          else reviewed)
            number = int(((saved.get('job') or {}).get('id') or 'job-0').rsplit('-', 1)[-1])+1
            result['job'] = {'id': f'job-{number}', 'action': 'finish', 'status': 'completed', 'result': answer}
            manifest = read_json(ctx.work_dir/'input/latest-finish.json', {})
            if answer.get('ok') and type(answer.get('passed')) is bool and manifest.get('passed') is answer['passed']:
                record_finish(result, manifest)
            else:
                result.update(status='blocked', phase='blocked', exit_code=2,
                              reason=answer.get('error') or 'existing media acceptance incomplete')
        result['updated_at'] = time.strftime('%F %T')
        atomic_write_json(ctx.run_dir/'agent-run.json', result)
    return result


@dataclass
class EpisodeRunState:
    ctx: EpisodeContext
    config: dict
    runtime: dict
    saved: dict
    result: dict
    finish_stamp: tuple | None
    deadline: float
    slice_limit: int | None
    slice_seconds: int | None
    slice_invocations: int
    wait_started: float
    wait_credited: float
    external_pipeline: bool
    native_mode: bool
    production_only: bool
    first_pass_only: bool
    budget_lock: object = field(default_factory=threading.Lock, repr=False)

    def account_resources(self):
        from novel_manga.resource_waits import wait_seconds
        waited=wait_seconds(self.ctx.run_dir/'resource-waits.jsonl',since=self.wait_started)
        extra=max(0,waited-self.wait_credited)
        self.wait_credited=waited
        self.deadline+=extra
        self.result['deadline']+=extra
        self.result['resource_wait_seconds']=round(self.saved.get('resource_wait_seconds',0)+waited,3)
        phase_budget.account(self.result.get('phase_budget'), self.deadline-time.monotonic())

    def begin_generation(self, persist):
        """Called by the existing provider hook, after all assets and probes are ready."""
        with self.budget_lock:
            budget = self.result.get('phase_budget')
            if budget is None or budget['phase'] == 'production':
                return
            self.account_resources()
            remaining = phase_budget.begin_generation(budget, self.deadline-time.monotonic())
            switched_at = time.time()
            budget['preparation']['finished_at'] = switched_at
            budget['production']['started_at'] = switched_at
            self.deadline = time.monotonic()+remaining
            self.result.update(deadline=switched_at+remaining, remaining_seconds=remaining)
            persist(deadline=self.result['deadline'], remaining_seconds=remaining, phase_budget=budget)

    def defer(self, reason):
        self.account_resources()
        self.result.update(status='deferred', phase='deferred', passed=False, exit_code=0,
                           reason=reason, remaining_seconds=max(0,self.deadline-time.monotonic()))


@dataclass(frozen=True)
class BootstrapResult:
    state: EpisodeRunState | None = None
    completed: dict | None = None


def start_run(ctx, *, config=None, timeout=None, retry_issues=False, tools_factory):
    from . import recovery
    config = load_config(ctx.project_root / 'configs/agent_sandbox.json') if config is None else config
    config = {**config, **{field: os.environ[var] for field, var in
        (('base_url', 'NOVEL_AGENT_BASE_URL'), ('model', 'NOVEL_AGENT_MODEL'),
         ('key_var', 'NOVEL_AGENT_KEY_VAR')) if os.environ.get(var)}}
    seconds = int(timeout if timeout is not None else config.get('timeout_seconds', DEFAULT_OPERATION_SECONDS))
    preparation_seconds = int(config.get('preparation_timeout_seconds', seconds))
    if seconds <= 0 or preparation_seconds <= 0:
        raise ValueError('timeout must be positive')
    runtime = resolve_agent_environment(ctx, config)
    if runtime.get('NOVEL_VIDEO_CHANNEL') and runtime.get('NOVEL_VIDEO_MODEL') in {'sd2.0-fast', 'sd2.0-mini'}:
        raise ValueError('SD Fast/Mini use the standard render_clips_thin.py --review workflow; this executor retains its H3 request contract')
    runtime['NOVEL_RESOURCE_WAIT_FILE']=str(ctx.run_dir/'resource-waits.jsonl')
    if config.get('refusal_fallback'):
        runtime['NOVEL_REFUSAL_FALLBACK'] = config['refusal_fallback'].get('endpoint', 'flashnext')
    external_pipeline = config.get('production_mode') == 'batch_review'
    native_mode = config.get('production_mode') in {'pipeline_review','batch_review'}
    production_only=native_mode and os.environ.get('NOVEL_PRODUCTION_ONLY')=='1'
    first_pass_only=native_mode and os.environ.get('NOVEL_FIRST_PASS_ONLY')=='1'
    if native_mode:
        runtime['NOVEL_AGENT_MANAGED'] = '1'
    prepare_inputs(ctx)
    if native_mode:
        manifest=read_json(ctx.work_dir/'input/manifest.json',{})
        phase=('review' if (ctx.run_dir/'pipeline-review-started.json').exists() else
               'input_repair' if (ctx.run_dir/'pipeline-input-repair-started.json').exists() else 'production')
        manifest.update(production_mode=config['production_mode'],memory_scope='repair_only',agent_phase=phase,
            authority='Fixed pipeline owns normal production. Agent repairs confirmed semantic input defects and reviews/repairs generated video.')
        atomic_write_json(ctx.work_dir/'input/manifest.json',manifest)
    from .local_preparation import stage
    stage(ctx)
    install_client(ctx.work_dir)
    saved = read_json(ctx.run_dir / 'agent-run.json', {})
    if not paused():
        saved = recovery.resume_source_review(ctx.run_dir, ctx.episode_dir, saved) or saved
        from .input_repair import repair as validate_input
        saved = recovery.resume_protocol(ctx.run_dir,ctx.episode_dir,saved,validate_input=validate_input) or saved
        saved = recovery.resume_handoff(ctx.run_dir, ctx.episode_dir, saved) or saved
        config,saved=recovery.protocol_fallback(ctx,config,saved)
    stale_completed=False
    if saved.get('status')=='finished' and saved.get('passed')is True:
        from novel_manga.application.production.runs import episode_status
        current=episode_status(ctx.episode_dir,True)
        stale_completed=current=='stale'
        if stale_completed:
            saved={**saved,'status':'finished_with_issues','passed':False,
                   'previous_delivery_status':current}
            if (saved.get('job')or{}).get('action')=='finish':saved['job']=None
    if config.get('finish_existing'):
        return BootstrapResult(completed=finish_existing(ctx, saved, runtime, tools_factory))
    if retry_issues:
        recovered = recovery.resume_summary_loop(ctx, config, saved)
        if recovered is not None:
            config, saved = recovered
    from .bounded_recovery import resume_window
    recovered_window=resume_window(ctx,saved)
    if recovered_window is not None:
        saved=recovered_window
        if saved.get('phase_budget'):
            budget=saved['phase_budget']
            row=budget[budget['phase']]
            row['limit_seconds']=max(row['limit_seconds'], row['used_seconds'])+saved['upstream_recovery']['seconds']
    if saved.get('upstream_recovery') and saved.get('status') in {'blocked','finished_with_issues'}:
        return BootstrapResult(completed=saved)
    if saved.get('status') == 'deferred':
        # Waiting behind other episodes consumes no active operation allowance.
        # Media, retry counters and the persisted Agent UUID are unchanged.
        saved['deadline'] = time.time() + max(0, float(saved['remaining_seconds']))
    elif (saved.get('phase_budget') and saved.get('status') in {'blocked', 'finished_with_issues'}
          and 'remaining_seconds' in saved):
        # A settled activity has stopped its clock. Preserve only its recorded
        # unused time while an operator reviews the result or authorizes a take.
        saved['deadline'] = time.time() + max(0, float(saved['remaining_seconds']))
    elif saved.get('status') == 'paused' and saved.get('updated_at') and saved.get('deadline'):
        # An operator pause is idle time, just like a scheduler deferral. Older
        # paused rows may still carry remaining_seconds from a previous slice.
        stopped_at = time.mktime(time.strptime(saved['updated_at'], '%Y-%m-%d %H:%M:%S'))
        saved['deadline'] = time.time() + max(0, float(saved['deadline'])-stopped_at)
    expired_operation = (saved.get('status') in {'running', 'retrying'}
                         and float(saved.get('deadline') or 0) <= time.time())
    if retry_issues and not saved.get('upstream_recovery') and (saved.get('status') in {'finished_with_issues', 'blocked'} or expired_operation):
        # A requested retry never renews video budgets. An interrupted author
        # can explicitly keep its session instead of rereading the same work.
        archive = ctx.run_dir/'retries'/str(time.time_ns())
        archive.mkdir(parents=True)
        atomic_write_json(archive/'agent-run.json', saved)
        session = ctx.work_dir/SESSION_FILE
        if session.is_file():
            if config.get('resume_session_on_retry'):
                shutil.copy2(session, archive/'episode-session.json')
            else:
                session.rename(archive/'episode-session.json')
        pending = saved.get('job') or {}
        saved = {**saved, 'agent_activity': archive.name, 'status': 'retrying',
                 'deadline': saved['deadline'] if saved.get('phase_budget') else time.time()+seconds,
                 'fallback_invocations': 0, 'no_progress': 0,
                 'stop_detail': None,
                 'job': pending if pending.get('status') in {'running', 'accepted'} else None}
        atomic_write_json(ctx.run_dir/'agent-run.json', saved)
    agent_activity = saved.get('agent_activity') or str(time.time_ns())
    if saved:
        saved = {**saved, 'agent_activity': agent_activity}
    config, saved = recovery.restore_model_route(config, saved)
    config = {**config, 'agent_activity': recovery.activity({**saved, 'agent_activity':agent_activity})}
    if saved.get('status') in {'finished', 'finished_with_issues'}:
        return BootstrapResult(completed=saved)
    previous_job = saved.get('job') or {}
    if (not stale_completed and previous_job.get('action') == 'finish' and previous_job.get('status') == 'completed'
            and previous_job.get('id') != (saved.get('upstream_recovery') or {}).get('prior_job_id')
            and type((previous_job.get('result') or {}).get('passed')) is bool):
        manifest = read_json(ctx.work_dir / 'input/latest-finish.json', {})
        current_session = read_json(ctx.work_dir / SESSION_FILE, {}).get('session_id')
        if (current_session and (manifest.get('author_session') or {}).get('session_id') == current_session
                and manifest.get('passed') is previous_job['result']['passed']):
            record_finish(saved, manifest)
            atomic_write_json(ctx.run_dir / 'agent-run.json', saved)
            return BootstrapResult(completed=saved)
    finish_stamp = progress_rules.file_stamp(ctx.work_dir / 'input/latest-finish.json')
    from novel_manga.application.production.artifacts import media_started
    budget = phase_budget.load(saved, preparation_seconds=preparation_seconds,
                               production_seconds=seconds, media_exists=media_started(ctx.episode_dir))
    result = {'episode': ctx.source_episode_dir.name, 'input_mode': ctx.input_mode,
              'agent_activity': agent_activity,
              'run_dir': str(ctx.run_dir), 'status': 'blocked', 'passed': False,
              'exit_code': 2, 'attempts': list(saved.get('attempts', [])), 'pid': os.getpid(),
              'deadline': saved.get('deadline') or time.time() + (budget[budget['phase']]['limit_seconds'] if budget else seconds),
              'fallback_invocations': saved.get('fallback_invocations', 0),
              'no_progress': saved.get('no_progress', 0), 'reason': None,
              'summary_only_turns': saved.get('summary_only_turns', 0),
              'idle_turns': saved.get('idle_turns',0),
              'agent_seconds': saved.get('agent_seconds', 0),
              'model_wait_seconds': saved.get('model_wait_seconds', 0)}
    if budget is not None:
        budget[budget['phase']].setdefault('started_at', result['deadline']-max(
            0, budget[budget['phase']]['limit_seconds']-budget[budget['phase']]['used_seconds']))
        result['phase_budget'] = budget
    if saved.get('upstream_recovery'):
        result['upstream_recovery']=saved['upstream_recovery']
    if saved.get('model_switch'):
        result['model_switch'] = saved['model_switch']
    if saved.get('source_review_recovery'):
        result['source_review_recovery'] = saved['source_review_recovery']
    if saved.get('protocol_recovery'):
        result['protocol_recovery']=saved['protocol_recovery']
    if saved.get('handoff_recovery'):
        result['handoff_recovery']=saved['handoff_recovery']
    for key in ('model_switch_history', 'invocation_retry_history', 'summary_context_recovery', 'upstream_recovery_history'):
        if key in saved:
            result[key] = saved[key]
    if saved.get('batch_handoff_at') is not None:
        result['batch_handoff_at'] = saved['batch_handoff_at']
    deadline = time.monotonic() + max(0, result['deadline'] - time.time())
    slice_limit = config.get('agent_invocations_per_slice')
    if slice_limit is not None and int(slice_limit) <= 0:
        raise ValueError('agent_invocations_per_slice must be positive')
    slice_seconds = config.get('agent_slice_seconds', DEFAULT_REPAIR_TURN_SECONDS)
    if slice_seconds is not None and int(slice_seconds) <= 0:
        raise ValueError('agent_slice_seconds must be positive')
    slice_invocations = 0
    from novel_manga.resource_waits import wait_seconds
    wait_started=time.monotonic()
    wait_credited=0.0

    return BootstrapResult(state=EpisodeRunState(ctx, config, runtime, saved, result, finish_stamp, deadline, slice_limit, slice_seconds, slice_invocations, wait_started, wait_credited, external_pipeline, native_mode, production_only, first_pass_only))
