"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations

import time


from .session import SESSION_FILE
from novel_manga.application.production.artifacts import media_started


from . import resume_inputs as resume_inputs
from . import lifecycle as lifecycle

def prepare_operations(ctx, bridge, state):
    config=state.config
    saved=state.saved
    native_mode=state.native_mode
    external_pipeline=state.external_pipeline
    production_only=state.production_only
    first_pass_only=state.first_pass_only
    previous_job=saved.get('job') or {}
    # Recover a submitted operation before asking the agent to do more.
    job = bridge.handle('status', {}).get('job') or {}
    if job.get('status') == 'accepted' and not lifecycle.paused():
        if (job['action'] == 'render' and previous_job.get('status') == 'running'
                and (ctx.run_dir / 'prepared.json').is_file()
                and media_started(ctx.episode_dir)):
            bridge._job['payload'] = {}
        bridge.end_turn()
    continued_inputs = False
    draft_check = None
    if (job.get('status') not in {'accepted', 'running'} and not lifecycle.paused()
            and time.monotonic() < state.deadline
            and resume_inputs.pending_input_continuations(ctx)):
        bridge.handle('apply', {'file': 'output/resume-inputs.json'})
        continuation = lifecycle.wait_for_idle(bridge)
        applied = (continuation.get('job') or {}).get('result') or {}
        continued_inputs = True
        if (time.monotonic() < state.deadline and (applied.get('changed') or applied.get('input_repaired'))
                and (not native_mode or media_started(ctx.episode_dir))):
            bridge.handle('render', {})
            bridge.update_state(phase='tool_running')
    if (not continued_inputs and (bridge.handle('status', {}).get('job') or {}).get('status')
            not in {'accepted', 'running'} and not lifecycle.paused() and time.monotonic() < state.deadline):
        if resume_inputs.stale_request_blocks(ctx) and (not native_mode or media_started(ctx.episode_dir)):
            bridge.handle('render', {})
            bridge.update_state(phase='tool_running')
            continued_inputs = True
        elif resume_inputs.pending_structured_repairs(ctx):
            bridge.handle('apply', {'file': 'output/resume-structured-repairs.json'})
            bridge.update_state(phase='tool_running')
            continued_inputs = True
    if not native_mode and not continued_inputs and (not saved or saved.get('status') == 'retrying') and not lifecycle.paused():
        bridge.update_state(phase='preparing_requests')
        if ((remaining := state.deadline-time.monotonic()) > 0 and not
                (ctx.input_mode == 'source' and config.get('agent_instructions')
                 and not (ctx.run_dir/'prepared.json').is_file())):
            draft_check = resume_inputs.fill_missing_requests(ctx, tools=bridge.tools, timeout=min(300, remaining))
        if (ctx.input_mode == 'script' and not (ctx.work_dir / SESSION_FILE).exists()
                and time.monotonic() < state.deadline):
            from .local_preparation import active
            checked = {'valid':True} if active(ctx) else bridge.handle('check', {})
            if checked.get('valid') is True:
                payload = {} if media_started(ctx.episode_dir) or active(ctx) else {'file': 'output/requests.json'}
                bridge.handle('render', payload)
                bridge.update_state(phase='tool_running')
    if (not native_mode and not continued_inputs and ctx.input_mode == 'source' and not lifecycle.paused()
            and time.monotonic() < state.deadline
            and (bridge.handle('status', {}).get('job') or {}).get('status') not in {'accepted', 'running'}
            and (ctx.work_dir/'output/episode.json').is_file()
            and not (ctx.run_dir/'prepared.json').is_file()):
        checked = draft_check if draft_check is not None else bridge.handle('check', {})
        if checked.get('valid') is True:
            # Editorial constraints or known asset corrections need an Agent
            # decision before media. Adoption alone does not submit video.
            bridge.handle('adopt' if config.get('agent_instructions') else 'render',
                          {'file': 'output/episode.json'})
            bridge.update_state(phase='tool_running')
    native_block = None
    native_wait = None
    if (native_mode and not external_pipeline and not lifecycle.paused() and (first_pass_only or not (ctx.run_dir/'pipeline-review-started.json').exists())
            and (not continued_inputs or not media_started(ctx.episode_dir))):
        from novel_manga.application.agents.episode import pipeline_preparation as native_episode
        lifecycle.wait_for_idle(bridge)
        bridge.update_state(phase='pipeline_preparation',waiting_for='native_pipeline')
        ready=native_episode.prepare(ctx,bridge.tools.drafts,bridge.tools.repairs)
        state.account_resources()
        if ready.get('status')=='waiting_assets':
            native_wait=ready
        elif ready.get('ready'):
            bridge.handle('render',{})
            bridge.update_state(phase='pipeline_generation',waiting_for='video')
        elif not ready.get('needs_semantic_repair'):
            native_block=ready
    return native_block, native_wait
