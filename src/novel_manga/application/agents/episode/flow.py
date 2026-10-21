"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time

from novel_manga.util import atomic_write_json, read_json

from .bridge import EpisodeBridge
from .session import SESSION_FILE
from .tools import EpisodeTools


from .limits import MAX_INVOCATIONS
from . import runtime as runtime_rules
from . import prompts as prompts
from . import progress as progress_rules
from . import lifecycle as lifecycle
from .startup import prepare_operations
from .turns import invoke


def run_episode(ctx, *, config=None, timeout=None, retry_issues=False):
    """One agent owns creative decisions; this controller only waits and resumes operations."""
    from novel_manga.llm import usage as llm_usage
    with lifecycle.episode_lock(ctx), llm_usage.scope(novel=ctx.episode_dir.parent.name, episode=ctx.episode_dir.name, stage='episode_agent'):
        result = _run_episode(ctx, config=config, timeout=timeout, retry_issues=retry_issues)
        from novel_manga.application.production.modelscope_delivery import deliver_if_configured
        return deliver_if_configured(ctx, result)


def _run_episode(ctx, *, config=None, timeout=None, retry_issues=False):
    from . import recovery
    initial=lifecycle.start_run(ctx,config=config,timeout=timeout,retry_issues=retry_issues,tools_factory=EpisodeTools)
    if initial.completed is not None:
        return initial.completed
    state=initial.state
    config=state.config
    runtime=state.runtime
    saved=state.saved
    result=state.result
    finish_stamp=state.finish_stamp
    slice_limit=state.slice_limit
    native_mode=state.native_mode
    external_pipeline=state.external_pipeline
    production_only=state.production_only
    first_pass_only=state.first_pass_only
    # bind's pathname is limited to 108 bytes on Linux. The socket still lives in
    # the mounted workspace; a directory fd keeps deep episode paths usable.
    directory_fd = os.open(ctx.work_dir, os.O_RDONLY | os.O_DIRECTORY)
    bridge = EpisodeBridge(EpisodeTools(ctx), Path(f'/proc/self/fd/{directory_fd}/episode-tools.sock'))
    bridge.tools.begin_generation = lambda: state.begin_generation(bridge.update_state)
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
    with runtime_rules.process_environment(runtime):
        try:
            thread.start()
            startup_deadline = time.monotonic() + 5
            while not bridge.ready.wait(0.05):
                if errors:
                    raise errors[0]
                if not thread.is_alive() or time.monotonic() >= startup_deadline:
                    raise RuntimeError('episode tool bridge did not start')
            from novel_manga.application.production.artifacts import media_started
            if media_started(ctx.episode_dir):
                state.begin_generation(bridge.update_state)
            native_block, native_wait = prepare_operations(ctx, bridge, state)
            no_progress = result['no_progress']
            fallbacks = result['fallback_invocations']
            while True:
                if lifecycle.paused() and (bridge.handle('status', {}).get('job') or {}).get('status') != 'running':
                    result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                    break
                status = lifecycle.wait_for_idle(bridge)
                state.account_resources()
                if external_pipeline:
                    from novel_manga.application.production.workspaces import pending_production
                    if pending_production(ctx):
                        state.defer('Updated first-pass inputs return to standard preparation and generation')
                        result.update(phase='waiting_pipeline',pipeline_phase='production',waiting_for='pipeline')
                        break
                if first_pass_only:
                    from novel_manga.application.preparation.store import unfilmed_video_clips
                    first_plan = read_json(ctx.episode_dir/'clip_plan.json', {})
                    if first_plan.get('clips') and not unfilmed_video_clips(ctx.episode_dir, first_plan):
                        state.defer('First videos complete; hand off film review to the repair queue')
                        result['pipeline_phase'] = 'review'
                        break
                if native_wait is not None:
                    state.defer('Shared batch assets pending; release the production host')
                    result.update(phase='waiting_assets',pipeline_phase='production',waiting_for='assets',
                                  waiting_assets=native_wait['waiting_assets'])
                    break
                if native_block is not None:
                    result.update(reason='native pipeline preparation blocked',pipeline_failure=native_block)
                    break
                manifest = lifecycle.current_finish(ctx, finish_stamp, status.get('job'))
                if manifest is not None:
                    lifecycle.record_finish(result, manifest)
                    break
                if lifecycle.paused():
                    result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                    break
                job = status.get('job') or {}
                if (not production_only and job.get('action') in {'render','review'} and job.get('status') == 'completed'
                        and (job.get('result') or {}).get('quality_passed') is True):
                    # The normal host operation already completed independent acceptance.
                    # Reserve an Agent conversation for an actual unresolved decision.
                    bridge.handle('finish', {})
                    status = lifecycle.wait_for_idle(bridge)
                    completed = status.get('job') or {}
                    manifest = read_json(ctx.work_dir / 'input/latest-finish.json', {})
                    if (completed.get('action') == 'finish' and completed.get('status') == 'completed'
                            and (completed.get('result') or {}).get('passed') is True
                            and manifest.get('passed') is True):
                        lifecycle.record_finish(result, manifest)
                        result['method'] = 'existing requests executed and accepted by host'
                        break
                outcome=job.get('result')or{}
                if (job.get('action') == 'apply' and job.get('status') == 'completed'
                        and outcome.get('media_operation') == 'crop' and outcome.get('review_required')):
                    bridge.handle('review', {})
                    bridge.update_state(phase='checking_existing_media')
                    continue
                if outcome.get('upstream_action')=='repair_assets' and outcome.get('input_decision') and outcome.get('ok'):
                    path='output/upstream-input-'+job['id']+'.json'
                    atomic_write_json(ctx.work_dir/path,{'decisions':[outcome['input_decision']]})
                    checked=bridge.tools.check({'file':path})
                    if checked.get('valid'):
                        bridge.handle('apply',{'file':path});bridge.update_state(phase='tool_running');continue
                    result['upstream_input_check']=checked
                if outcome.get('repacked'):
                    from .pipeline_preparation import prepare_repacked
                    prepared=prepare_repacked(ctx,outcome.get('changed',[]))
                    if not prepared.get('ready'):
                        result['repacked_preparation']=prepared
                        job={**job,'result':{**outcome,'changed':[]}}
                if (progress_rules.applied_repair(job)
                        and (time.monotonic() < state.deadline or config.get('complete_started_repairs'))):
                    bridge.handle('render', {})
                    bridge.update_state(phase='tool_running')
                    continue
                from .bounded_recovery import invocation_limit_reached
                if invocation_limit_reached(result):
                    finished=bridge.tools.dispatch('finish',{})
                    if finished.get('passed'):
                        lifecycle.record_finish(result,read_json(ctx.work_dir/'input/latest-finish.json',{}))
                    else:
                        result.update(reason='bounded recovery completed two proposal turns',bounded_finish=finished)
                    break
                before = progress_rules.progress(ctx, status)
                before_status = status
                remaining = int(state.deadline - time.monotonic())
                if remaining <= 0:
                    result['reason'] = 'episode operation timeout exhausted'
                    break
                if slice_limit is not None and state.slice_invocations >= int(slice_limit):
                    state.defer('Agent slice completed; resume this session after other production work')
                    break
                if native_mode:
                    from novel_manga.application.agents.episode.pipeline_preparation import (
                        reviewable, semantic_pending, semantic_prompt, begin_semantic_repair, review_prompt, begin_review)
                    batch_failure=external_pipeline and read_json(ctx.episode_dir/'batch-production.json',{}).get('status')=='blocked'
                    semantic=(batch_failure and first_pass_only) or (
                        not reviewable(ctx) and (semantic_pending(ctx) or batch_failure))
                    if not reviewable(ctx) and not semantic:
                        result.update(reason='native pipeline generation produced no reviewable video',pipeline_failure=job.get('result'))
                        break
                    if (begin_semantic_repair(ctx) if semantic else begin_review(ctx)):
                        no_progress=fallbacks=0
                        result.update(no_progress=0,fallback_invocations=0)
                    prompt=semantic_prompt() if semantic else review_prompt()
                    draft_exception=external_pipeline and semantic and not (ctx.episode_dir/'clip_plan.json').exists()
                    if draft_exception:
                        prompt=prompts.initial_prompt(ctx)+'\nThe standard planner already failed. Repair its existing draft and recorded errors; do not repeat completed book preparation.'
                    if first_pass_only:
                        missing = sorted(unfilmed_video_clips(ctx.episode_dir))
                        if not draft_exception:
                            prompt = (semantic_prompt() if semantic else
                                'Complete only the missing first video clips using the adopted screenplay and current input checks. '
                                'Fix confirmed input issues through apply, then render. Existing videos wait for later review. ')
                        prompt += ('\nThis is first video production. Targets: '+json.dumps(missing)+'. '
                            'Do not review/finish the film, keep/recheck filmed clips, or order retakes. '
                            'When no legal input correction remains, report the concrete blocker; do not repeat exhausted operations.')
                    phase='input_repair' if semantic else 'review'
                    result['pipeline_phase']=phase
                    manifest=read_json(ctx.work_dir/'input/manifest.json',{})
                    atomic_write_json(ctx.work_dir/'input/manifest.json',{**manifest,'agent_phase':phase})
                    bridge.update_state(pipeline_phase=phase)
                    if production_only and not first_pass_only:
                        state.defer('Native production handed off to the independent repair queue')
                        break
                else:
                    resume = (ctx.work_dir / SESSION_FILE).is_file()
                    prompt = prompts.continuation_prompt() if resume or (ctx.run_dir/'prepared.json').is_file() else prompts.initial_prompt(ctx)
                resume = (ctx.work_dir / SESSION_FILE).is_file()
                from .session import current_context
                continuing = resume and current_context(ctx.work_dir, config)
                if continuing:
                    prompt = prompts.continuation_prompt()
                if config.get('agent_instructions') and not continuing:
                    prompt += '\nTask-specific instructions:\n' + str(config['agent_instructions'])
                diagnosis = recovery.diagnose(ctx, status)
                atomic_write_json(ctx.run_dir/'recovery-diagnosis.json', diagnosis)
                memories = recovery.recall(ctx, diagnosis)
                from .current_task import prepare as current_task, prompt_view
                status['upstream_route']=recovery.upstream_route(ctx,status)
                task=current_task(ctx,status,diagnosis,memories)
                prompt += ('\nCurrent task (input/current-task.json); full state is in input/current-status.json. '
                           'Use current targets and exact choices, not historical instructions:\n'
                           +json.dumps(prompt_view(task, continuing=continuing),ensure_ascii=False))
                attempt, session_timeout, remaining = invoke(ctx, bridge, state, config, prompt, resume, remaining)
                result['attempts'].append(str(attempt.directory))
                bridge.update_state(attempts=result['attempts'], phase='tool_running',
                                    agent_seconds=result['agent_seconds'],
                                    session_id=read_json(ctx.work_dir / SESSION_FILE, {}).get('session_id'))
                if lifecycle.paused() and (bridge.handle('status', {}).get('job') or {}).get('status') == 'accepted':
                    result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                    break
                status = lifecycle.wait_for_idle(bridge)
                if lifecycle.paused():
                    result.update(status='paused', phase='paused', reason='production dispatch paused', exit_code=0)
                    break
                manifest = lifecycle.current_finish(ctx, finish_stamp, status.get('job'))
                if manifest is not None:
                    lifecycle.record_finish(result, manifest)
                    break
                handed_off = bool((status.get('job') or {}).get('id') and
                                  (status.get('job') or {}).get('id') != (before_status.get('job') or {}).get('id'))
                if not attempt.ok and handed_off:
                    # The host operation already ran; its result, not a CLI retry, is next.
                    continue
                current_remaining = int(state.deadline - time.monotonic())
                replacement = recovery.switch_for_refusal(ctx, config, result, attempt, before_status, status, remaining=current_remaining)
                if replacement is not None:
                    config = replacement
                    no_progress = 0
                    fallbacks = result['fallback_invocations']
                    bridge.update_state(model_switch=result['model_switch'], no_progress=0)
                    continue
                if config.get('complete_started_repairs') and progress_rules.applied_repair(status.get('job') or {}):
                    # The submitted repair is already validated and published.
                    # Let the host close its media chain even if the author CLI
                    # ended at its timeout while handing off that operation.
                    no_progress = 0
                    result['no_progress'] = 0
                    bridge.update_state(no_progress=0)
                    continue
                if not attempt.ok:
                    replacement = recovery.retry_failed_invocation(config, result, attempt, current_remaining)
                    if replacement is not None:
                        config = replacement
                        fallbacks = result['fallback_invocations']
                        bridge.update_state(fallback_invocations=fallbacks)
                        continue
                    timed_out = attempt.exit_code == 124 and session_timeout < remaining
                    if timed_out or attempt.record.get('turn_limit_reached'):
                        no_progress = 0 if progress_rules.made_progress(ctx, before, before_status, status) else no_progress + 1
                        result['no_progress'] = no_progress
                        if no_progress >= 2:
                            result['reason'] = 'episode agent ended twice without operation or output progress'
                        else:
                            state.defer('Agent reached this slice '+('time' if timed_out else 'turn')+' limit; continue the saved session')
                        break
                    result.update(reason='episode agent invocation failed', session_exit=attempt.exit_code)
                    break
                job_before = before[-2]
                job = status.get('job') or {}
                handed_off = bool(job.get('id') and job['id'] != job_before)
                if not handed_off:
                    fallbacks += 1
                no_progress = 0 if progress_rules.made_progress(ctx, before, before_status, status) else no_progress + 1
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
            bridge.end_turn(dispatch=not lifecycle.paused())
            bridge.stop()
            if thread.ident is not None:
                # Bridge shutdown waits for its one accepted host job before removing its socket.
                thread.join()
            os.close(directory_fd)
        if result.get('reason') in {'episode agent ended twice without operation or output progress',
                                   'episode agent reached the bounded operation continuation limit'}:
            result['stop_detail'] = progress_rules.stop_details(status)
        if result.get('status') != 'deferred':
            recovery.remember(ctx, read_json(ctx.run_dir/'recovery-diagnosis.json', {}),
                {'action': 'finish' if result.get('passed') else 'stop',
                 'ok': bool(result.get('passed')), 'passed': bool(result.get('passed')),
                 'error': result.get('reason')})
        result['phase'] = result.get('phase') or ('finished' if result['status'].startswith('finished') else 'blocked')
        state.account_resources()
        result['remaining_seconds'] = max(0, state.deadline-time.monotonic())
        bridge.update_state(**result)
        result = read_json(ctx.run_dir / 'agent-run.json')
    return result
