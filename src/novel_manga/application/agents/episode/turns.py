"""Run one episode agent while its existing host tools execute media operations."""
from __future__ import annotations

import time

from novel_manga.util import read_json

from .session import run_session


from .limits import DEFAULT_SOURCE_TURN_SECONDS
from novel_manga.llm.resources import model_slot
from . import lifecycle as lifecycle

def invoke(ctx, bridge, state, config, prompt, resume, remaining):
    result=state.result
    slice_seconds=state.slice_seconds
    waiting_since = None
    def waiting():
        nonlocal waiting_since
        waiting_since = time.monotonic()
        bridge.update_state(phase='waiting_resource', waiting_for='agent_model')
        if lifecycle.paused():
            raise InterruptedError('production dispatch paused')
    def account_wait():
        nonlocal waiting_since
        if waiting_since is None:
            return
        waited = max(0, time.monotonic() - waiting_since)
        waiting_since = None
        # Shared capacity is queue time, not additional author work.
        # Keep all previously used operation time and retry counters.
        state.deadline += waited
        result['deadline'] += waited
        result['model_wait_seconds'] = round(result.get('model_wait_seconds', 0) + waited, 3)
        bridge.update_state(deadline=result['deadline'],
                            model_wait_seconds=result['model_wait_seconds'])
    bridge.update_state(phase='ready_agent', waiting_for=None)
    try:
        with model_slot(config['base_url'], on_wait=waiting, cancelled=lifecycle.paused,
                        reserve=int(config.get('model_slots_reserved_for_tools', 0)),record_queue=False):
            account_wait()
            if lifecycle.paused():
                raise InterruptedError('production dispatch paused')
            remaining = int(state.deadline - time.monotonic())
            if remaining <= 0:
                raise TimeoutError('episode operation timeout exhausted')
            bridge.update_state(phase='agent_running', waiting_for=None)
            bridge.begin_turn()
            started = time.monotonic()
            # Initial adaptation includes reading the source and pictures and
            # writing a whole screenplay. It is not a short repair turn.
            direct_draft = ((ctx.work_dir/'output/episode.json').is_file()
                            and read_json(ctx.run_dir/'source-draft-call.json', {}).get('status') == 'candidate')
            authoring = (ctx.input_mode == 'source' and not (ctx.run_dir/'prepared.json').is_file()
                         and not direct_draft)
            turn_config = dict(config)
            turn_seconds = config.get('source_agent_slice_seconds', config.get(
                'agent_slice_seconds', DEFAULT_SOURCE_TURN_SECONDS)) if authoring else slice_seconds
            if authoring and config.get('source_agent_max_turns') is not None:
                turn_config['max_turns'] = config['source_agent_max_turns']
            session_timeout = min(remaining, int(turn_seconds)) if turn_seconds is not None else remaining
            attempt = run_session(ctx.work_dir, prompt, resume=resume, timeout=session_timeout,
                config=turn_config, enable_tools=True, project_root=ctx.project_root,
                expected_output='requests.json' if ctx.input_mode == 'script' else 'episode.json')
            state.slice_invocations += 1
            result['agent_seconds'] += round(time.monotonic() - started, 3)
    finally:
        account_wait()
        # The external CLI is gone and its model slot is now free.
        # A review may call that very same model service.
        bridge.end_turn(dispatch=not lifecycle.paused())
    return attempt, session_timeout, remaining
