"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time

from novel_manga.application.agents.sandbox import load_config
from novel_manga.application.configuration import environment, repair_environment, RuntimePaths
from novel_manga.llm.config import ENDPOINTS
from novel_manga.util import atomic_write_json, read_json

from .bridge import EpisodeBridge, install_client
from .inputs import prepare_inputs
from .session import SESSION_FILE, run_session
from .tools import EpisodeTools

MAX_INVOCATIONS = 3


@contextmanager
def process_environment(values):
    """This sequential command owns its environment until its host jobs have settled."""
    before = dict(os.environ)
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)


def runtime_environment(ctx, config):
    env = environment(ctx.project_root)
    pipeline = read_json(ctx.project_root / 'configs/pipeline.json', {})
    if not env.get('NOVEL_LOCAL_H3_URL', '').strip():
        env, settings = repair_environment(ctx.project_root, ctx.run_dir)
        # The pool belongs to the service, not this episode's sandbox directory.
        key = next((row for row in pipeline.get('resources', {}).get('video_keys', {}).values()
                    if row.get('pool') == settings['pool']), {})
        env['NOVEL_INFLIGHT_DIR'] = str(key.get('inflight_dir') or
            RuntimePaths(ctx.project_root).temporary / 'inflight' / settings['pool'])
        env.setdefault('NOVEL_RENDER_INFLIGHT', str((key.get('inflight') or {}).get('max', settings['inflight'])))
    elif not env.get('NOVEL_RENDER_INFLIGHT'):
        key = next((row for row in pipeline.get('resources', {}).get('video_keys', {}).values()
                    if row.get('base_url') == env['NOVEL_LOCAL_H3_URL']), {})
        env['NOVEL_RENDER_INFLIGHT'] = str((key.get('inflight') or {}).get('max', 24))
        env.setdefault('NOVEL_INFLIGHT_DIR', str(key.get('inflight_dir') or
            RuntimePaths(ctx.project_root).temporary / 'inflight' / (key.get('pool') or 'h3pool')))
    from novel_manga.application.configuration import model_resources_environment
    for name, value in model_resources_environment(ctx.project_root, pipeline).items():
        env.setdefault(name, value)
    base = config['base_url'].rstrip('/')
    if not base.endswith('/v1'):
        base += '/v1'
    defaults = {'QWEN38_LOCAL_BASE_URL': base, 'QWEN38_LOCAL_MODEL': config['model'],
                'QWEN38_LOCAL_API_KEY_VAR': config['key_var'], 'QWEN38_LOCAL_STREAM': '0'}
    for key, value in defaults.items():
        env[key] = os.environ.get(key) or value
    preset = next((name for name, values in ENDPOINTS.items()
                   if all(values.get(key) == env[key] for key in
                          ('QWEN38_LOCAL_BASE_URL', 'QWEN38_LOCAL_MODEL',
                           'QWEN38_LOCAL_API_KEY_VAR'))), None)
    preset = preset or os.environ.get('SECOND_REVIEW_JUDGE') or os.environ.get('NOVEL_REVIEW_VIDEO_JUDGE')
    if preset is None:
        raise ValueError('The configured sandbox endpoint has no existing QA preset; '
                         'set SECOND_REVIEW_JUDGE and NOVEL_REVIEW_VIDEO_JUDGE to existing presets.')
    env['SECOND_REVIEW_JUDGE'] = os.environ.get('SECOND_REVIEW_JUDGE') or preset
    env['NOVEL_REVIEW_VIDEO_JUDGE'] = os.environ.get('NOVEL_REVIEW_VIDEO_JUDGE') or preset
    env['NOVEL_PROJECT_ROOT'] = str(ctx.project_root)
    env['NOVEL_CLIP_SECONDS_MAX'] = str(ctx.clip_seconds_max)
    return env


def initial_prompt(ctx):
    common = (
        'Read input/manifest.json and the episode-production skill/output contract. '
        'Inspect the supplied actual reference images. Use the retained production profile, '
        'speech rules, source evidence and remaining generation budgets. '
        'Use python3 /work/tools/episode.py to check, render, review, apply supported repairs, '
        'and finish this episode. When a tool returns yield=true, end this CLI turn immediately. '
        'The host will execute it and resume this same session with the result; it owns waiting. '
        'Read the actual latest frames, ASR and '
        'independent verdicts. Finish with the actual accepted or blocked outcome.\n'
    )
    if ctx.input_mode == 'script':
        return common + (
            'This is script input mode. The existing approved chapter_script.json, clip_plan.json '
            'and selected assets are authoritative. Preserve every shot, story event, exact dialogue '
            'and speaker, timing, camera, reference selection/order and source binding. '
            'Do not replan, rewrite the screenplay, replace assets or impose a new episode length. '
            'Draft and inspect only the final English H3 requests for the supplied clips. '
            'Write output/requests.json using the request contract, perform your own semantic/image '
            'review, run check, and execute the existing episode through the supplied tools. '
            'Keep repairs within the existing protected request contract.\n'
        )
    target = ctx.target_seconds
    duration = (f'{target[0]:g}–{target[1]:g} seconds' if target else
                'the duration specified in input/manifest.json')
    return common + (
        'This is source input mode. Read the complete original chapter. Create the episode '
        'screenplay, ordered shots and complete final H3 requests yourself from that source and '
        f'the supplied assets. Aim for {duration}, with each clip no longer than '
        f'{ctx.clip_seconds_max:g} seconds. Preserve source-to-shot evidence and meaningful '
        'adaptation choices. Write output/episode.json using the output contract, self-review '
        'the complete episode, check it, and execute it through the supplied tools.\n'
    )


def continuation_prompt():
    return (
        'Continue this same saved episode session and existing workflow. The previous CLI turn '
        'ended and the host operation has now settled. Read python3 /work/tools/episode.py status '
        'and the saved latest operation results, then continue from the current episode state. '
        'When a tool returns yield=true, end this turn so the host can execute it. '
        'Complete the remaining checks, review or supported repair within the existing budget '
        'and input authority. Run finish when accepted or when the episode must stop, and '
        'end this turn after its acceptance; the host will complete it.\n'
    )


def _file_stamp(path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return info.st_mtime_ns, info.st_size


def _progress(ctx, status):
    paths = [ctx.work_dir / name for name in (
        'output/episode.json', 'output/requests.json', 'output/repair.json',
        'input/latest-render.json', 'input/latest-review.json', 'input/latest-finish.json')]
    paths.extend(ctx.run_dir / name for name in ('prepared.json', 'tool-repair-decisions.json'))
    # These small JSON outputs carry actual progress. Equal-size edits can keep
    # the same timestamp, and touching an unchanged draft is not progress.
    outputs = []
    for path in paths:
        try:
            outputs.append(path.read_bytes())
        except FileNotFoundError:
            outputs.append(None)
    job = status.get('job') or {}
    return tuple(outputs), job.get('id'), job.get('status')


def _wait_for_idle(bridge):
    status = bridge.handle('status', {})
    next_update = time.monotonic() + 30
    while status.get('busy'):
        if time.monotonic() >= next_update:
            job = status.get('job') or {}
            print(f"Waiting for episode operation {job.get('id')}: {job.get('action')}", flush=True)
            next_update = time.monotonic() + 30
        time.sleep(0.2)
        status = bridge.handle('status', {})
    return status


def _paused():
    path = os.environ.get('NOVEL_AGENT_PAUSE_FILE')
    return bool(path and Path(path).exists())


@contextmanager
def _episode_lock(ctx):
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


def _current_finish(ctx, previous_stamp):
    path = ctx.work_dir / 'input/latest-finish.json'
    stamp = _file_stamp(path)
    if stamp is None or stamp == previous_stamp:
        return None
    manifest = read_json(path, None)
    if not isinstance(manifest, dict):
        return None
    current = read_json(ctx.work_dir / SESSION_FILE, {}).get('session_id')
    if not current or (manifest.get('author_session') or {}).get('session_id') != current:
        return None
    return manifest


def _record_finish(result, manifest):
    passed = manifest.get('passed') is True
    result.update(status='finished' if passed else 'finished_with_issues', phase='finished',
                  passed=passed, exit_code=0 if passed else 1, finish=manifest)


def read_status(ctx):
    """Read a prepared or running episode; never start a model, bridge or media task."""
    return {'episode': ctx.source_episode_dir.name, 'input_mode': ctx.input_mode,
            'run_dir': str(ctx.run_dir), 'execution': read_json(ctx.run_dir / 'execution.json', {}),
            'agent_run': read_json(ctx.run_dir / 'agent-run.json', {}),
            'session': read_json(ctx.work_dir / SESSION_FILE, {}),
            'finish': read_json(ctx.work_dir / 'input/latest-finish.json', None)}


def run_episode(ctx, *, config=None, timeout=None):
    """One agent owns creative decisions; this controller only waits and resumes operations."""
    with _episode_lock(ctx):
        return _run_episode(ctx, config=config, timeout=timeout)


def _run_episode(ctx, *, config=None, timeout=None):
    from novel_manga.llm.resources import model_slot
    config = load_config(ctx.project_root / 'configs/agent_sandbox.json') if config is None else config
    config = {**config, **{field: os.environ[var] for field, var in
        (('base_url', 'NOVEL_AGENT_BASE_URL'), ('model', 'NOVEL_AGENT_MODEL'),
         ('key_var', 'NOVEL_AGENT_KEY_VAR')) if os.environ.get(var)}}
    seconds = int(timeout if timeout is not None else config.get('timeout_seconds', 5400))
    if seconds <= 0:
        raise ValueError('timeout must be positive')
    runtime = runtime_environment(ctx, config)
    prepare_inputs(ctx)
    install_client(ctx.work_dir)
    saved = read_json(ctx.run_dir / 'agent-run.json', {})
    if saved.get('status') in {'finished', 'finished_with_issues'}:
        return saved
    previous_job = saved.get('job') or {}
    if previous_job.get('action') == 'finish' and previous_job.get('status') == 'completed':
        manifest = read_json(ctx.work_dir / 'input/latest-finish.json', {})
        current_session = read_json(ctx.work_dir / SESSION_FILE, {}).get('session_id')
        if current_session and (manifest.get('author_session') or {}).get('session_id') == current_session:
            _record_finish(saved, manifest)
            atomic_write_json(ctx.run_dir / 'agent-run.json', saved)
            return saved
    finish_stamp = _file_stamp(ctx.work_dir / 'input/latest-finish.json')
    result = {'episode': ctx.source_episode_dir.name, 'input_mode': ctx.input_mode,
              'run_dir': str(ctx.run_dir), 'status': 'blocked', 'passed': False,
              'exit_code': 2, 'attempts': list(saved.get('attempts', [])), 'pid': os.getpid(),
              'deadline': saved.get('deadline') or time.time() + seconds,
              'fallback_invocations': saved.get('fallback_invocations', 0),
              'no_progress': saved.get('no_progress', 0), 'reason': None,
              'agent_seconds': saved.get('agent_seconds', 0)}
    deadline = time.monotonic() + max(0, result['deadline'] - time.time())
    # bind's pathname is limited to 108 bytes on Linux. The socket still lives in
    # the mounted workspace; a directory fd keeps deep episode paths usable.
    directory_fd = os.open(ctx.work_dir, os.O_RDONLY | os.O_DIRECTORY)
    bridge = EpisodeBridge(EpisodeTools(ctx), Path(f'/proc/self/fd/{directory_fd}/episode-tools.sock'))
    bridge.bind_state(ctx.run_dir / 'agent-run.json')
    bridge.update_state(**{**result, 'status': 'running'}, phase='ready_agent')
    socket_path = ctx.work_dir / 'episode-tools.sock'
    from novel_manga.application.production.common import pid_alive
    if saved.get('pid') and not pid_alive(saved['pid']):
        if socket_path.is_socket():
            socket_path.unlink()
    errors = []

    def serve():
        try:
            bridge.serve()
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=serve, name=f'episode-tools-{ctx.source_episode_dir.name}')
    with process_environment(runtime):
        try:
            thread.start()
            startup_deadline = time.monotonic() + 5
            while not bridge.ready.wait(0.05):
                if errors:
                    raise errors[0]
                if not thread.is_alive() or time.monotonic() >= startup_deadline:
                    raise RuntimeError('episode tool bridge did not start')
            # Recover a submitted operation before asking the agent to do more.
            job = bridge.handle('status', {}).get('job') or {}
            if job.get('status') == 'accepted' and not _paused():
                if (job['action'] == 'render' and previous_job.get('status') == 'running'
                        and (ctx.run_dir / 'prepared.json').is_file()
                        and bridge.tools._main_media_started()):
                    bridge._job['payload'] = {}
                bridge.end_turn()
            no_progress = result['no_progress']
            fallbacks = result['fallback_invocations']
            while True:
                if _paused() and (bridge.handle('status', {}).get('job') or {}).get('status') != 'running':
                    result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                    break
                status = _wait_for_idle(bridge)
                manifest = _current_finish(ctx, finish_stamp)
                if manifest is not None:
                    _record_finish(result, manifest)
                    break
                if _paused():
                    result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                    break
                before = _progress(ctx, status)
                remaining = int(deadline - time.monotonic())
                if remaining <= 0:
                    result['reason'] = 'episode operation timeout exhausted'
                    break
                resume = (ctx.work_dir / SESSION_FILE).is_file()
                prompt = continuation_prompt() if resume else initial_prompt(ctx)
                def waiting():
                    bridge.update_state(phase='waiting_resource', waiting_for='agent_model')
                    if _paused():
                        raise InterruptedError('production dispatch paused')
                bridge.update_state(phase='ready_agent', waiting_for=None)
                try:
                    with model_slot(config['base_url'], deadline=deadline, on_wait=waiting, cancelled=_paused):
                        if _paused():
                            result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                            break
                        remaining = int(deadline - time.monotonic())
                        if remaining <= 0:
                            raise TimeoutError('episode operation timeout exhausted')
                        bridge.update_state(phase='agent_running', waiting_for=None)
                        bridge.begin_turn()
                        started = time.monotonic()
                        attempt = run_session(ctx.work_dir, prompt, resume=resume, timeout=remaining,
                            config=config, enable_tools=True, project_root=ctx.project_root,
                            expected_output='requests.json' if ctx.input_mode == 'script' else 'episode.json')
                        result['agent_seconds'] += round(time.monotonic() - started, 3)
                finally:
                    # The external CLI is gone and its model slot is now free.
                    # A review may call that very same model service.
                    bridge.end_turn(dispatch=not _paused())
                result['attempts'].append(str(attempt.directory))
                bridge.update_state(attempts=result['attempts'], phase='tool_running',
                                    session_id=read_json(ctx.work_dir / SESSION_FILE, {}).get('session_id'))
                if _paused() and (bridge.handle('status', {}).get('job') or {}).get('status') == 'accepted':
                    result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                    break
                status = _wait_for_idle(bridge)
                if not attempt.ok:
                    result.update(reason='episode agent invocation failed', session_exit=attempt.exit_code)
                    break
                manifest = _current_finish(ctx, finish_stamp)
                if manifest is not None:
                    _record_finish(result, manifest)
                    break
                job_before = before[-2]
                job = status.get('job') or {}
                handed_off = bool(job.get('id') and job['id'] != job_before)
                if not handed_off:
                    fallbacks += 1
                no_progress = no_progress + 1 if before == _progress(ctx, status) else 0
                result.update(no_progress=no_progress, fallback_invocations=fallbacks)
                bridge.update_state(no_progress=no_progress, fallback_invocations=fallbacks)
                if no_progress >= 2:
                    result['reason'] = 'episode agent ended twice without operation or output progress'
                    break
                if fallbacks >= MAX_INVOCATIONS:
                    result['reason'] = 'episode agent reached the bounded operation continuation limit'
                    break
        except InterruptedError:
            result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
        except Exception as error:
            result['reason'] = f'{type(error).__name__}: {error}'
        finally:
            bridge.end_turn(dispatch=not _paused())
            bridge.stop()
            if thread.ident is not None:
                # Bridge shutdown waits for its one accepted host job before removing its socket.
                thread.join()
            os.close(directory_fd)
        result['phase'] = result.get('phase') or ('finished' if result['status'].startswith('finished') else 'blocked')
        bridge.update_state(**result)
        result = read_json(ctx.run_dir / 'agent-run.json')
    return result
