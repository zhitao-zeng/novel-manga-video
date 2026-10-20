"""Episode handoffs release resources while retaining session and operation ownership."""
from novel_manga.application.agents.episode import turns as episode_turns
from novel_manga.application.agents.episode import resume_inputs as episode_resume_inputs
from novel_manga.application.agents.episode import prompts as episode_prompts
from novel_manga.application.agents.episode import progress as episode_progress
import json
import os
from pathlib import Path
import threading
import time
from dataclasses import replace

import pytest

from novel_manga.application.agents.episode import flow
from novel_manga.llm.resources import model_slot
from novel_manga.util import atomic_write_json, read_json
from support.episode_runtime import episode_context as ctx, sandbox_config as config, fake_attempt, install_fake_runtime


@pytest.mark.parametrize('production_only',[False,True])
def test_pipeline_review_invokes_agent_only_after_native_generation(ctx,config,monkeypatch,production_only):
    from novel_manga.application.agents.episode import pipeline_preparation as native_episode
    calls=install_fake_runtime(monkeypatch,ctx)
    if production_only:monkeypatch.setenv('NOVEL_PRODUCTION_ONLY','1')
    bridges=capture_bridge(monkeypatch)
    original=flow.EpisodeTools
    class Tools(original):
        def dispatch(self,action,payload):
            result=super().dispatch(action,payload)
            if action=='render':
                result['quality_passed']=False  # Unaccepted media needs the review Agent.
                video=ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4'
                video.parent.mkdir(parents=True,exist_ok=True);video.write_bytes(b'generated video')
            return result
    monkeypatch.setattr(flow,'EpisodeTools',Tools)
    monkeypatch.setattr(native_episode,'prepare',lambda *a:calls.append('native_prepare') or {'ready':True})
    monkeypatch.setattr(episode_resume_inputs,'fill_missing_requests',lambda *a,**kw:pytest.fail('legacy free-form source path'))
    def session(work,prompt,**kwargs):
        assert not production_only, 'production host must yield before taking a repair slot'
        assert calls.index('native_prepare')<calls.index('render')
        assert 'review and repair' in prompt.lower()
        calls.append('review_agent')
        atomic_write_json(work/'episode-session.json',{'session_id':'same-session'})
        bridges[-1].handle('finish',{})
        return fake_attempt(ctx,1)
    monkeypatch.setattr(episode_turns,'run_session',session)
    result=flow.run_episode(ctx,config={**config,'production_mode':'pipeline_review'})
    if production_only:
        assert result['status']=='deferred' and result['pipeline_phase']=='review'
        assert not result['passed'] and result['remaining_seconds']>0
        assert 'review_agent' not in calls
    else:
        assert result['passed'] and calls.count('review_agent')==1


def test_pipeline_failure_is_recorded_without_agent_authoring(ctx,config,monkeypatch):
    from novel_manga.application.agents.episode import pipeline_preparation as native_episode
    calls=install_fake_runtime(monkeypatch,ctx)
    monkeypatch.setattr(native_episode,'prepare',lambda *a:{'ready':False,'stage':'preparation','error':'missing card'})
    monkeypatch.setattr(episode_turns,'run_session',lambda *a,**kw:pytest.fail('Agent before video'))
    result=flow.run_episode(ctx,config={**config,'production_mode':'pipeline_review'})
    assert result['status']=='blocked' and result['pipeline_failure']['error']=='missing card'
    assert 'render' not in calls


def test_resumed_unfilmed_input_correction_returns_to_native_preparation(ctx,config,monkeypatch):
    from novel_manga.application.agents.episode import pipeline_preparation as native_episode
    calls=install_fake_runtime(monkeypatch,ctx);original=flow.EpisodeTools
    class Tools(original):
        def dispatch(self,action,payload):
            result=super().dispatch(action,payload)
            if action=='apply':result.update(changed=['clip_01'],input_repaired=['clip_01'])
            return result
    monkeypatch.setattr(flow,'EpisodeTools',Tools)
    monkeypatch.setattr(episode_resume_inputs,'pending_input_continuations',lambda *a:True)
    monkeypatch.setattr(native_episode,'prepare',lambda *a:calls.append('native_prepare') or {'ready':False,'stage':'preparation'})
    monkeypatch.setattr(episode_turns,'run_session',lambda *a,**kw:pytest.fail('Agent before video'))
    result=flow.run_episode(ctx,config={**config,'production_mode':'pipeline_review'})
    assert calls.index('apply')<calls.index('native_prepare')
    assert 'render' not in calls and result['status']=='blocked'


@pytest.mark.parametrize('production_only',[False,True])
def test_confirmed_semantic_exception_can_start_repair_agent_before_video(ctx,config,monkeypatch,production_only):
    from novel_manga.application.agents.episode import pipeline_preparation as native_episode
    calls=install_fake_runtime(monkeypatch,ctx)
    if production_only:monkeypatch.setenv('NOVEL_PRODUCTION_ONLY','1')
    def prepare(*unused):
        failure={'status':'needs_repair','audit':{'source_readable':True,
            'issues':[{'kind':'speaker','stage':1,'problem':'private thought became public'}]}}
        row={'status':'semantic_repair','ready':False,'needs_semantic_repair':True,'failure':failure}
        atomic_write_json(ctx.run_dir/'pipeline-production.json',row)
        return row
    monkeypatch.setattr(native_episode,'prepare',prepare)
    def session(work,prompt,**kwargs):
        assert not production_only, 'semantic repair must acquire separate repair admission'
        assert 'targeted repair Agent' in prompt and 'shots[].turns' in prompt
        assert 'render' not in calls
        assert read_json(work/'input/manifest.json')['agent_phase']=='input_repair'
        calls.append('semantic_agent')
        atomic_write_json(work/'episode-session.json',{'session_id':'same-session'})
        return fake_attempt(ctx,1)
    monkeypatch.setattr(episode_turns,'run_session',session)
    result=flow.run_episode(ctx,config={**config,'production_mode':'pipeline_review','agent_invocations_per_slice':1})
    assert calls.count('semantic_agent')==int(not production_only) and result['status']=='deferred'
    assert result.get('pipeline_phase')=='input_repair'


def test_bulk_asset_wait_defers_without_agent_or_generation(ctx,config,monkeypatch):
    from novel_manga.application.agents.episode import pipeline_preparation as native_episode
    calls=install_fake_runtime(monkeypatch,ctx)
    monkeypatch.setattr(native_episode,'prepare',lambda *a:{'ready':False,'status':'waiting_assets',
        'waiting_assets':['/book/series_assets/locations/location_001/establishing.jpeg']})
    monkeypatch.setattr(episode_turns,'run_session',lambda *a,**k:pytest.fail('image waiting is not repair work'))
    result=flow.run_episode(ctx,config={**config,'production_mode':'pipeline_review'})
    assert result['status']=='deferred' and result['phase']=='waiting_assets'
    assert result['pipeline_phase']=='production' and result['remaining_seconds']>0
    assert 'render' not in calls


def test_first_pass_semantic_exception_can_repair_inputs_in_production_host(ctx, config, monkeypatch):
    from novel_manga.application.agents.episode import pipeline_preparation as native_episode
    install_fake_runtime(monkeypatch, ctx)
    monkeypatch.setenv('NOVEL_FIRST_PASS_ONLY', '1')
    monkeypatch.setenv('NOVEL_PRODUCTION_ONLY', '1')
    failure = {'status': 'needs_repair', 'audit': {'issues': [{'kind': 'speaker', 'stage': 1}]}}
    def prepare(*args):
        row = {'status': 'semantic_repair', 'ready': False, 'needs_semantic_repair': True, 'failure': failure}
        atomic_write_json(ctx.run_dir/'pipeline-production.json', row)
        atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': [{'clip_id': 'clip_01', 'kind': 'video'}]})
        return row
    monkeypatch.setattr(native_episode, 'prepare', prepare)
    seen = []
    def session(work, prompt, **kwargs):
        assert 'first video' in prompt and 'clip_01' in prompt
        seen.append(1)
        return fake_attempt(ctx, len(seen))
    monkeypatch.setattr(episode_turns, 'run_session', session)
    result = flow.run_episode(ctx, config={**config, 'production_mode': 'pipeline_review', 'agent_invocations_per_slice': 1})
    assert seen == [1] and result['status'] == 'deferred'


def test_mixed_first_pass_renders_before_review_and_hands_off_without_agent(ctx, config, monkeypatch):
    from novel_manga.application.agents.episode import pipeline_preparation as native_episode
    calls = install_fake_runtime(monkeypatch, ctx)
    monkeypatch.setenv('NOVEL_FIRST_PASS_ONLY', '1')
    monkeypatch.setenv('NOVEL_PRODUCTION_ONLY', '1')
    atomic_write_json(ctx.run_dir/'pipeline-review-started.json', {'started': True})
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips': [{'clip_id': 'clip_01', 'kind': 'video'}]})
    original = flow.EpisodeTools
    class Tools(original):
        def dispatch(self, action, payload):
            result = super().dispatch(action, payload)
            if action == 'render':
                path = ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4'
                path.parent.mkdir(parents=True); path.write_bytes(b'first video')
            return result
    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(native_episode, 'prepare', lambda *a: {'ready': True})
    monkeypatch.setattr(episode_turns, 'run_session', lambda *a, **k: pytest.fail('post-render Agent stole first-pass capacity'))
    result = flow.run_episode(ctx, config={**config, 'production_mode': 'pipeline_review'})
    assert calls.count('render') == 1 and result['status'] == 'deferred'
    assert result['pipeline_phase'] == 'review' and not result['passed']


def test_external_batch_review_starts_at_agent_without_native_production(ctx, config, monkeypatch):
    from novel_manga.application.agents.episode import pipeline_preparation as native_episode
    calls=install_fake_runtime(monkeypatch,ctx)
    video=ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4';video.parent.mkdir(parents=True);video.write_bytes(b'pipeline video')
    atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[{'clip_id':'clip_01','kind':'video'}]})
    atomic_write_json(ctx.run_dir/'agent-run.json',{'status':'deferred','remaining_seconds':100,'batch_handoff_at':1234})
    monkeypatch.setattr(native_episode,'prepare',lambda *a:pytest.fail('Agent review repeated normal production'))
    bridges=capture_bridge(monkeypatch)
    def session(work,prompt,**kw):
        assert 'review and repair' in prompt.lower()
        atomic_write_json(work/'episode-session.json',{'session_id':'review-session'})
        bridges[-1].handle('finish',{})
        return fake_attempt(ctx,1)
    monkeypatch.setattr(episode_turns,'run_session',session)
    result=flow.run_episode(ctx,config={**config,'production_mode':'batch_review'})
    assert result['passed'] and 'render' not in calls and result['batch_handoff_at']==1234


def test_batch_input_repair_yields_updated_plan_to_standard_producer(ctx,config,monkeypatch):
    from novel_manga.application.production import workspaces
    calls=install_fake_runtime(monkeypatch,ctx)
    plan={'clips':[{'clip_id':'clip_01','kind':'video','references':[]}]}
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    row={k:str(getattr(ctx,k)) for k in ('run_dir','novel_dir','episode_dir','source_novel_dir','source_episode_dir')}
    workspaces.save(row,'blocked',error='wrong binding')
    original=flow.EpisodeTools
    class Tools(original):
        def dispatch(self,action,payload):
            result=super().dispatch(action,payload)
            if action=='apply':
                atomic_write_json(ctx.episode_dir/'clip_plan.json',{**plan,'binding':'corrected'})
                result.update(changed=['clip_01'],input_repaired=['clip_01'])
            return result
    monkeypatch.setattr(flow,'EpisodeTools',Tools)
    bridges=capture_bridge(monkeypatch)
    def session(work,prompt,**kw):
        bridges[-1].handle('apply',{'file':'output/repair.json'})
        return fake_attempt(ctx,1)
    monkeypatch.setattr(episode_turns,'run_session',session)
    result=flow.run_episode(ctx,config={**config,'production_mode':'batch_review','agent_invocations_per_slice':1})
    assert result['status']=='deferred' and result['phase']=='waiting_pipeline'
    assert 'render' not in calls and result['remaining_seconds']>0
    assert workspaces.current(row) is None


@pytest.mark.parametrize('missing_plan',[False,True])
def test_batch_first_input_agent_repairs_missing_inputs_before_film_review(ctx,config,monkeypatch,missing_plan):
    from novel_manga.application.production import workspaces
    install_fake_runtime(monkeypatch,ctx)
    monkeypatch.setenv('NOVEL_FIRST_PASS_ONLY','1')
    if not missing_plan:
        atomic_write_json(ctx.episode_dir/'clip_plan.json',{'clips':[
            {'clip_id':'clip_01','kind':'video','references':[]},
            {'clip_id':'clip_02','kind':'video','references':[]}]})
        p=ctx.episode_dir/'work/clips/clip_01/attempt_01/clip.mp4';p.parent.mkdir(parents=True);p.write_bytes(b'old video')
    row={k:str(getattr(ctx,k)) for k in ('run_dir','novel_dir','episode_dir','source_novel_dir','source_episode_dir')}
    workspaces.save(row,'blocked',error='planner failed' if missing_plan else 'clip_02 request binding')
    seen=[]
    def session(work,prompt,**kw):
        assert read_json(work/'input/manifest.json')['agent_phase']=='input_repair'
        assert 'Do not review/finish' in prompt
        if missing_plan:assert 'The standard planner already failed' in prompt
        else:assert 'Targets: ["clip_02"]' in prompt
        seen.append(prompt)
        return fake_attempt(ctx,1)
    monkeypatch.setattr(episode_turns,'run_session',session)
    result=flow.run_episode(ctx,config={**config,'production_mode':'batch_review','agent_invocations_per_slice':1})
    assert result['status']=='deferred' and seen


def test_stop_details_distinguish_host_error_from_empty_response_with_budget_left():
    status = {'remaining_takes': {'clip_01': 1}, 'remaining_rechecks': {'clip_01': 1},
              'input_repairs': {'clip_01': {'status': 'tool_error', 'step': 'compile', 'error': 'KeyError: actor'}},
              'job': {'action': 'apply', 'result': {'blocked': {'clip_01': 'actor'}}}}
    details = episode_progress.stop_details(status)
    assert details['kind'] == 'tool_error'
    assert details['remaining_takes']['clip_01'] == 1
    assert details['input_repairs']['clip_01']['step'] == 'compile'
    empty = episode_progress.stop_details({'remaining_takes': {'clip_01': 1}, 'job': None})
    assert empty['kind'] == 'no_effective_operation'


def test_task_instructions_prevent_unguarded_automatic_source_authoring(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    bridges = capture_bridge(monkeypatch)
    settings = {**config, 'agent_instructions': 'Use the approved nonsexual adaptation.'}
    monkeypatch.setattr(episode_resume_inputs, 'fill_missing_requests', lambda *a, **kw: pytest.fail('automatic writer ignored task instructions'))
    def run(work, prompt, **kwargs):
        assert settings['agent_instructions'] in prompt
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        bridges[-1].handle('finish', {})
        return fake_attempt(ctx, 1)
    monkeypatch.setattr(episode_turns, 'run_session', run)
    assert flow.run_episode(ctx, config=settings)['passed']


def test_operator_pause_preserves_unspent_time_even_after_wall_deadline(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    bridges = capture_bridge(monkeypatch)
    stopped = time.time()-7200
    atomic_write_json(ctx.run_dir/'agent-run.json', {'status': 'paused', 'deadline': stopped+600,
        'updated_at': time.strftime('%F %T', time.localtime(stopped)), 'remaining_seconds': 20})
    def run(work, prompt, **kwargs):
        assert kwargs['timeout'] > 580
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        bridges[-1].handle('finish', {})
        return fake_attempt(ctx, 1)
    monkeypatch.setattr(episode_turns, 'run_session', run)
    result = flow.run_episode(ctx, config=config)
    assert result['passed']


def test_instructed_source_candidate_is_adopted_before_any_render(ctx, config, monkeypatch):
    calls = install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools.dispatch
    def dispatch(self, action, payload):
        result = original(self, action, payload)
        if action == 'check':result['valid'] = True
        if action == 'adopt':atomic_write_json(ctx.run_dir/'prepared.json', {'ready': True})
        return result
    monkeypatch.setattr(flow.EpisodeTools, 'dispatch', dispatch)
    bridges = capture_bridge(monkeypatch)
    atomic_write_json(ctx.work_dir/'output/episode.json', {'candidate':True})
    settings = {**config, 'agent_instructions':'Prepare the known wardrobe correction before filming.'}
    def run(work, prompt, **kwargs):
        assert 'adopt' in calls and 'render' not in calls
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        bridges[-1].handle('finish', {})
        return fake_attempt(ctx, 1)
    monkeypatch.setattr(episode_turns, 'run_session', run)
    assert flow.run_episode(ctx, config=settings)['passed']


def capture_bridge(monkeypatch):
    instances = []
    original = flow.EpisodeBridge

    def create(*args):
        instance = original(*args)
        instances.append(instance)
        return instance

    monkeypatch.setattr(flow, 'EpisodeBridge', create)
    return instances


@pytest.mark.parametrize('previous_error', ["'prompt_h3'", 'source decision rejected'])
def test_malformed_saved_repair_does_not_crash_automatic_recovery(ctx, previous_error):
    atomic_write_json(ctx.run_dir/'tool-repair-decisions.json', [
        {'blocked': {'clip_01': previous_error}}])
    path = ctx.work_dir/'output/repair.json'
    atomic_write_json(path, [{'clip_id': 'clip_01', 'action': 'edit', 'h3': {'shots': ['A hall.']}}])
    before = path.read_bytes()
    assert episode_resume_inputs.pending_structured_repairs(ctx) is False
    assert path.read_bytes() == before
    assert not (ctx.work_dir/'output/resume-structured-repairs.json').exists()


@pytest.mark.parametrize('prepared,turn_seconds', [(True, 600), (False, 1200)])
def test_production_default_windows_apply_to_new_episode_tasks(ctx, config, monkeypatch, prepared, turn_seconds):
    install_fake_runtime(monkeypatch, ctx)
    bridges = capture_bridge(monkeypatch)
    if prepared:
        atomic_write_json(ctx.run_dir/'prepared.json', {'ready': True})
    settings = {k: v for k, v in config.items() if k != 'timeout_seconds'}
    settings['agent_instructions'] = 'User-approved editorial constraints must remain in force.'
    def run(work, prompt, **kwargs):
        assert kwargs['timeout'] == turn_seconds
        assert settings['agent_instructions'] in prompt
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        bridges[-1].handle('finish', {})
        return fake_attempt(ctx, 1)
    monkeypatch.setattr(episode_turns, 'run_session', run)
    result = flow.run_episode(ctx, config=settings)
    assert result['passed']
    assert 5390 < result['deadline']-time.time() <= 5400


@pytest.mark.parametrize('cancelled', [False, True])
def test_model_queue_wait_preserves_only_unused_operation_time(ctx, config, monkeypatch, cancelled):
    from contextlib import contextmanager
    from novel_manga.llm import resources
    install_fake_runtime(monkeypatch, ctx)
    bridges = capture_bridge(monkeypatch)
    atomic_write_json(ctx.run_dir/'agent-run.json', {'model_wait_seconds': 7, 'status': 'retrying'})
    real_clock, offset = time.monotonic, [0]
    monkeypatch.setattr(flow.time, 'monotonic', lambda: real_clock() + offset[0])
    def prepare(*args, **kwargs):
        offset[0] += 8  # Actual preparation has already used part of the 30-second window.
    monkeypatch.setattr(episode_resume_inputs, 'fill_missing_requests', prepare)
    @contextmanager
    def occupied(*args, deadline=None, on_wait=None, **kwargs):
        on_wait()
        offset[0] += 90
        if cancelled:
            raise InterruptedError('production dispatch paused')
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('model resource wait budget exhausted')
        yield None
    monkeypatch.setattr(episode_turns, 'model_slot', occupied)
    calls = []
    def run(work, prompt, **kwargs):
        calls.append(kwargs)
        assert 20 <= kwargs['timeout'] <= 22
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        bridges[-1].handle('finish', {})
        return fake_attempt(ctx, 1)
    monkeypatch.setattr(episode_turns, 'run_session', run)
    result = flow.run_episode(ctx, config=config)
    assert 97 <= result['model_wait_seconds'] < 98
    if cancelled:
        assert result['status'] == 'paused' and not calls
    else:
        assert result['passed'] and len(calls) == 1


def test_normal_source_draft_can_render_and_finish_without_any_agent_session(ctx, config, monkeypatch):
    from novel_manga.application.agents.episode import authoring
    calls = install_fake_runtime(monkeypatch, ctx)
    atomic_write_json(ctx.work_dir/'input/manifest.json', {'ready': True})
    original = flow.EpisodeTools

    class Tools(original):
        def dispatch(self, action, payload):
            result = super().dispatch(action, payload)
            if action == 'check':
                result['valid'] = True
            if action == 'render':
                result['quality_passed'] = True
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    drafts = []

    def propose(context, **kw):
        drafts.append(kw)
        assert 0 < kw['timeout'] <= config['timeout_seconds']
        atomic_write_json(context.work_dir/'output/episode.json', {'draft': 'complete'})
        return {'status': 'candidate'}

    monkeypatch.setattr(authoring, 'propose', propose)
    monkeypatch.setattr(episode_turns, 'run_session', lambda *a, **k: pytest.fail('normal accepted episode needs no Agent'))
    result = flow.run_episode(ctx, config=config)
    assert result['passed'] and len(drafts) == 1
    assert calls.count('check') == calls.count('render') == calls.count('finish') == 1
    assert result['attempts'] == []


def test_source_draft_at_deadline_does_not_start_render(ctx, config, monkeypatch):
    from novel_manga.application.agents.episode import authoring
    calls = install_fake_runtime(monkeypatch, ctx)
    atomic_write_json(ctx.work_dir/'input/manifest.json', {'ready': True})
    real_monotonic, offset = time.monotonic, [0]
    monkeypatch.setattr(flow.time, 'monotonic', lambda: real_monotonic()+offset[0])
    original = flow.EpisodeTools

    class Tools(original):
        def dispatch(self, action, payload):
            result = super().dispatch(action, payload)
            if action == 'check':
                result['valid'] = True
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)

    def propose(context, **kwargs):
        atomic_write_json(context.work_dir/'output/episode.json', {'draft': 'complete'})
        offset[0] = config['timeout_seconds']+1
        return {'status': 'candidate'}

    monkeypatch.setattr(authoring, 'propose', propose)
    monkeypatch.setattr(episode_turns, 'run_session', lambda *a, **k: pytest.fail('deadline already exhausted'))
    result = flow.run_episode(ctx, config=config)
    assert result['reason'] == 'episode operation timeout exhausted'
    assert 'render' not in calls


def test_completed_input_repair_after_deadline_is_retained_without_starting_new_video(ctx, config, monkeypatch):
    calls = install_fake_runtime(monkeypatch, ctx)
    bridges = capture_bridge(monkeypatch)
    real_monotonic, offset = time.monotonic, [0]
    monkeypatch.setattr(flow.time, 'monotonic', lambda: real_monotonic()+offset[0])
    original = flow.EpisodeTools

    class Tools(original):
        def dispatch(self, action, payload):
            result = super().dispatch(action, payload)
            if action == 'apply':
                offset[0] = config['timeout_seconds']+1
                result['input_repaired'] = ['clip_01']
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)

    def run(work, prompt, **kw):
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        bridges[-1].handle('apply', {})
        return fake_attempt(ctx, 1)

    monkeypatch.setattr(episode_turns, 'run_session', run)
    result = flow.run_episode(ctx, config=config)
    assert result['job']['status'] == 'completed'
    assert result['job']['result']['input_repaired'] == ['clip_01']
    assert result['reason'] == 'episode operation timeout exhausted'
    assert 'render' not in calls


@pytest.mark.parametrize('changed', [True, False])
@pytest.mark.parametrize('exit_code', [0, 124])
def test_configured_started_repair_can_finish_after_agent_window_without_new_turn(ctx, config, monkeypatch, changed, exit_code):
    calls=install_fake_runtime(monkeypatch, ctx)
    bridges=capture_bridge(monkeypatch)
    real_monotonic, offset=time.monotonic,[0]
    monkeypatch.setattr(flow.time, 'monotonic', lambda:real_monotonic()+offset[0])
    original=flow.EpisodeTools
    class Tools(original):
        def dispatch(self, action, payload):
            result=super().dispatch(action,payload)
            if action=='apply':
                offset[0]=config['timeout_seconds']+1
                result['input_repaired']=['clip_01'] if changed else []
            if action=='render':result['quality_passed']=True
            return result
    monkeypatch.setattr(flow,'EpisodeTools',Tools)
    turns=[]
    def run(work,prompt,**kwargs):
        turns.append(kwargs)
        atomic_write_json(work/'episode-session.json',{'session_id':'same-session'})
        bridges[-1].handle('apply',{})
        return fake_attempt(ctx,1,exit_code=exit_code)
    monkeypatch.setattr(episode_turns,'run_session',run)
    result=flow.run_episode(ctx,config={**config,'complete_started_repairs':True})
    assert len(turns)==1
    assert result['passed'] is changed
    assert calls.count('render')==calls.count('finish')==int(changed)
    assert read_json(ctx.work_dir/'episode-session.json')['session_id']=='same-session'


@pytest.mark.parametrize('source_passed,resumes,expected', [(True,0,True),(True,1,False),(False,0,False)])
def test_pending_asset_tool_resumes_saved_source_checked_work_once(ctx, source_passed, resumes, expected):
    row={'clip_id':'clip_01','action':'input','reason':'source clothing','source_quote':'original words',
         'phases':{'characters':{'甲':{'base_costume':'black cloak'}}}}
    receipt=ctx.episode_dir/'work/input_repairs/clip_01-1.json'
    atomic_write_json(receipt,{'clip_id':'clip_01','status':'incomplete','step':'assets',
        'source':{'consistent':source_passed},'resume_count':resumes,'decision':row})
    (ctx.work_dir/'output').mkdir(parents=True)
    before=receipt.read_bytes()
    assert episode_resume_inputs.pending_input_continuations(ctx) is expected
    assert receipt.read_bytes()==before
    if expected:
        request=read_json(ctx.work_dir/'output/resume-inputs.json')['decisions'][0]
        assert request['resume'] is True and request['clip_id']=='clip_01'


def test_pause_during_completed_repair_survives_author_timeout_and_resumes_same_window(ctx, config, monkeypatch):
    calls=install_fake_runtime(monkeypatch,ctx)
    bridges=capture_bridge(monkeypatch)
    marker=ctx.run_dir/'pause'
    monkeypatch.setenv('NOVEL_AGENT_PAUSE_FILE',str(marker))
    real_monotonic,offset=time.monotonic,[0]
    monkeypatch.setattr(flow.time,'monotonic',lambda:real_monotonic()+offset[0])
    original=flow.EpisodeTools
    class Tools(original):
        def dispatch(self,action,payload):
            result=super().dispatch(action,payload)
            if action=='apply':
                marker.write_text('pause')
                offset[0]=config['timeout_seconds']+1
                result['input_repaired']=['clip_01']
            if action=='render':result['quality_passed']=True
            return result
    monkeypatch.setattr(flow,'EpisodeTools',Tools)
    def author(work,prompt,**kwargs):
        atomic_write_json(work/'episode-session.json',{'session_id':'same-session'})
        bridges[-1].handle('apply',{})
        return fake_attempt(ctx,1,exit_code=124)
    monkeypatch.setattr(episode_turns,'run_session',author)
    settings={**config,'complete_started_repairs':True}
    paused=flow.run_episode(ctx,config=settings)
    assert paused['status']=='paused' and 'render' not in calls
    assert paused['job']['status']=='completed'
    deadline=paused['deadline']
    marker.unlink()
    monkeypatch.setattr(episode_turns,'run_session',lambda *a,**k:pytest.fail('no new author window'))
    resumed=flow.run_episode(ctx,config=settings)
    assert resumed['passed'] and 0 <= resumed['deadline']-deadline < 2
    assert calls.count('apply')==calls.count('render')==calls.count('finish')==1
    assert read_json(ctx.work_dir/'episode-session.json')['session_id']=='same-session'


def test_slice_drains_accepted_work_then_resumes_same_session_and_budget(ctx, config, monkeypatch):
    calls = install_fake_runtime(monkeypatch, ctx)
    bridges = capture_bridge(monkeypatch)
    options = {**config, 'agent_invocations_per_slice': 1}
    sessions = []
    def run(work, prompt, **kw):
        sessions.append(kw)
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        bridges[-1].handle('render' if len(sessions) == 1 else 'finish', {})
        return fake_attempt(ctx, len(sessions))
    monkeypatch.setattr(episode_turns, 'run_session', run)
    result = flow.run_episode(ctx, config=options)
    assert result['status'] == 'deferred' and result['passed'] is False
    assert calls.count('render') == 1 and calls.count('finish') == 0
    assert result['job']['status'] == 'completed'
    assert 0 < result['remaining_seconds'] <= config['timeout_seconds']
    result['deadline'] = time.time()-60
    atomic_write_json(ctx.run_dir/'agent-run.json', result)
    finished = flow.run_episode(ctx, config=options)
    assert finished['passed'] is True and sessions[-1]['resume'] is True
    assert calls.count('render') == 1 and calls.count('finish') == 1
    assert len(finished['attempts']) == 2


def test_cli_turn_limit_preserves_session_and_counts_an_empty_slice(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    def run(work, prompt, **kw):
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        attempt = fake_attempt(ctx, 1, exit_code=1)
        attempt.record['turn_limit_reached'] = True
        return attempt
    monkeypatch.setattr(episode_turns, 'run_session', run)
    result = flow.run_episode(ctx, config={**config, 'agent_invocations_per_slice': 1})
    assert result['status'] == 'deferred' and result['exit_code'] == 0
    assert result['no_progress'] == 1 and result['fallback_invocations'] == 0
    assert (ctx.work_dir/'episode-session.json').exists()


def test_normal_resumption_is_not_stopped_by_an_old_total_invocation_setting(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    bridges = capture_bridge(monkeypatch)
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(ctx.run_dir/'agent-run.json', {
        'status': 'deferred', 'remaining_seconds': 500, 'attempts': ['old-1', 'old-2'],
        'agent_seconds': 17, 'deadline': time.time()-100})
    def run(work, prompt, **kw):
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        bridges[-1].handle('finish', {})
        return fake_attempt(ctx, 3)
    monkeypatch.setattr(episode_turns, 'run_session', run)
    result = flow.run_episode(ctx, config={**config, 'max_agent_invocations': 2})
    assert result['passed'] is True and result['agent_seconds'] >= 17
    assert result['attempts'][:2] == ['old-1', 'old-2']
    assert len(result['attempts']) == 3


def test_repeating_the_same_failed_operation_is_not_progress_from_a_new_job_id(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools
    class Tools(original):
        def dispatch(self, action, payload):
            if action == 'apply':
                return {'ok': False, 'changed': [], 'blocked': {'clip_01': 'same rejected request'}}
            return super().dispatch(action, payload)
    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    bridges = capture_bridge(monkeypatch)
    calls = []
    def run(work, prompt, **kw):
        calls.append(1)
        assert len(calls) <= 4, 'rejected operation loop was not stopped'
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        atomic_write_json(work/'output/repair.json', {'reason': f'rephrased justification {len(calls)}'})
        bridges[-1].handle('apply', {})
        return fake_attempt(ctx, len(calls))
    monkeypatch.setattr(episode_turns, 'run_session', run)
    result = flow.run_episode(ctx, config=config)
    assert result['status'] == 'blocked' and result['no_progress'] == 2
    assert len(calls) == 2


def test_cached_review_with_a_new_job_id_is_not_progress(ctx):
    old = {'job': {'id': 'job-1', 'action': 'review', 'status': 'completed',
                   'result': {'ok': True, 'feedback': {'clip_01': 'same issue'}}}}
    new = {'job': {**old['job'], 'id': 'job-2'}}
    before = episode_progress.progress(ctx, old)
    assert not episode_progress.made_progress(ctx, before, old, new)
    new['job']['result'] = {'ok': True, 'feedback': {}}
    assert episode_progress.made_progress(ctx, before, old, new)


def test_doctor_report_and_new_job_id_are_not_content_repair_progress(ctx):
    old={'job':{'id':'job-1','action':'doctor','status':'completed','result':{'ok':True,'report':{'ok':False}}}}
    new={'job':{'id':'job-2','action':'doctor','status':'completed','result':{'ok':True,'report':{'ok':True}}}}
    before=episode_progress.progress(ctx,old)
    assert not episode_progress.made_progress(ctx,before,old,new)
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[{'clip_id':'c','action':'input','reason':'source correction'}]})
    assert episode_progress.made_progress(ctx,before,old,new)


@pytest.mark.parametrize('status', ['completed', 'failed'])
def test_different_failed_operations_stop_across_slices(ctx, config, monkeypatch, status):
    install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools
    calls = []
    class Tools(original):
        def dispatch(self, action, payload):
            if action == 'apply':
                message = f'input repair allowance used; explanation {len(calls)}'
                if status == 'failed':
                    raise ValueError(message)
                return {'ok': False, 'changed': [], 'blocked': {'clip_01': message}}
            return super().dispatch(action, payload)
    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    bridges = capture_bridge(monkeypatch)
    def session(work, prompt, **options):
        calls.append(1)
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        atomic_write_json(work/'output/repair.json', {'reason': f'new explanation {len(calls)}'})
        bridges[-1].handle('apply', {})
        return fake_attempt(ctx, len(calls))
    monkeypatch.setattr(episode_turns, 'run_session', session)
    options = {**config, 'agent_invocations_per_slice': 1}
    first = flow.run_episode(ctx, config=options)
    assert first['status'] == 'deferred' and first['no_progress'] == 1
    second = flow.run_episode(ctx, config=options)
    assert second['status'] == 'blocked' and second['no_progress'] == 2
    assert len(calls) == 2


def test_reused_input_receipt_does_not_reset_progress(ctx):
    old = {'job': {'id': 'job-1', 'action': 'apply', 'status': 'completed', 'result': {}}}
    new = {'job': {'id': 'job-2', 'action': 'apply', 'status': 'completed', 'result': {
        'ok': True, 'changed': [], 'input_repaired': ['clip_01'], 'input_reused': ['clip_01']}}}
    before = episode_progress.progress(ctx, old)
    assert not episode_progress.made_progress(ctx, before, old, new)
    assert not episode_progress.applied_repair(new['job'])
    new['job']['result'].update(ok=False, changed=['clip_02'], blocked={'clip_03': 'still broken'})
    assert episode_progress.made_progress(ctx, before, old, new)
    assert episode_progress.applied_repair(new['job'])


def test_changing_argument_error_text_without_repair_is_not_progress(ctx):
    old={'job':{'id':'job-1','action':'apply','status':'completed','result':{
        'ok':False,'changed':[],'argument_errors':{'clip_01':'bad wears'},'blocked':{'clip_01':'bad wears'}}}}
    new={'job':{'id':'job-2','action':'apply','status':'completed','result':{
        'ok':False,'changed':[],'argument_errors':{'clip_02':'bad phase'},'blocked':{'clip_02':'bad phase'}}}}
    before=episode_progress.progress(ctx,old)
    assert not episode_progress.made_progress(ctx,before,old,new)
    new['job']['result']['changed']=['clip_03']
    assert episode_progress.made_progress(ctx,before,old,new)


def test_argument_only_failures_stop_across_slices_without_resetting_allowances(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch,ctx)
    original=flow.EpisodeTools
    calls=[]
    class Tools(original):
        def dispatch(self,action,payload=None):
            if action=='apply':
                message=f'bad argument field {len(calls)}'
                return {'ok':False,'changed':[],'argument_errors':{'clip_01':message},
                        'blocked':{'clip_01':message}}
            return super().dispatch(action,payload)
    monkeypatch.setattr(flow,'EpisodeTools',Tools)
    bridges=capture_bridge(monkeypatch)
    def run(work,prompt,**options):
        calls.append(1)
        atomic_write_json(work/'episode-session.json',{'session_id':'same-session'})
        atomic_write_json(work/'output/repair.json',{'reason':f'rephrased {len(calls)}'})
        bridges[-1].handle('apply',{})
        return fake_attempt(ctx,len(calls))
    monkeypatch.setattr(episode_turns,'run_session',run)
    options={**config,'agent_invocations_per_slice':1}
    first=flow.run_episode(ctx,config=options)
    assert first['status']=='deferred' and first['no_progress']==1
    second=flow.run_episode(ctx,config=options)
    assert second['status']=='blocked' and second['no_progress']==2 and len(calls)==2


def test_slow_cli_yields_at_slice_timeout_without_resetting_episode_budget(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    def run(work, prompt, **kw):
        assert kw['timeout'] == 5
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        return fake_attempt(ctx, 1, exit_code=124)
    monkeypatch.setattr(episode_turns, 'run_session', run)
    result = flow.run_episode(ctx, config={**config, 'agent_slice_seconds': 5})
    assert result['status'] == 'deferred' and result['passed'] is False
    assert len(result['attempts']) == 1 and result['remaining_seconds'] <= 30


def test_repeated_time_slices_without_saved_work_stop_instead_of_requeueing_forever(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    def run(work, prompt, **kw):
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        return fake_attempt(ctx, 1, exit_code=124)
    monkeypatch.setattr(episode_turns, 'run_session', run)
    first = flow.run_episode(ctx, config={**config, 'agent_slice_seconds': 5})
    assert first['status'] == 'deferred'
    second = flow.run_episode(ctx, config={**config, 'agent_slice_seconds': 5})
    assert second['status'] == 'blocked' and second['no_progress'] == 2
    assert 'without operation or output progress' in second['reason']


@pytest.mark.parametrize('prepared', [False, True])
@pytest.mark.parametrize('direct', [False, True])
def test_source_authoring_has_its_own_budget_until_plan_is_adopted(ctx, config, monkeypatch, prepared, direct):
    calls = install_fake_runtime(monkeypatch, ctx)
    bridges = capture_bridge(monkeypatch)
    if prepared:
        atomic_write_json(ctx.run_dir/'prepared.json', {'ready': True})
    if direct:
        atomic_write_json(ctx.run_dir/'source-draft-call.json', {'status': 'candidate'})
        atomic_write_json(ctx.work_dir/'output/episode.json', {'draft': 'existing source draft'})
    def run(work, prompt, **kw):
        assert kw['timeout'] == (5 if prepared or direct else 20)
        assert kw['config']['max_turns'] == (4 if prepared or direct else 12)
        if prepared:
            assert prompt.startswith(episode_prompts.continuation_prompt())
        elif direct:
            assert 'handling an exception' in prompt
            assert 'relevant episode-production contract sections' in prompt
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        bridges[-1].handle('finish', {})
        return fake_attempt(ctx, 1)
    monkeypatch.setattr(episode_turns, 'run_session', run)
    result = flow.run_episode(ctx, config={**config, 'agent_slice_seconds': 5,
        'source_agent_slice_seconds': 20, 'max_turns': 4, 'source_agent_max_turns': 12})
    assert result['passed'] is True and calls.count('finish') == 1


def test_valid_existing_requests_finish_after_host_acceptance_without_agent(ctx, config, monkeypatch):
    ctx = replace(ctx, input_mode='script')
    calls = install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools

    class Tools(original):
        def dispatch(self, action, payload):
            result = super().dispatch(action, payload)
            if action == 'check':
                result['valid'] = True
            if action == 'render':
                result['quality_passed'] = True
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(episode_turns, 'run_session', lambda *a, **k: pytest.fail('accepted existing requests need no Agent'))
    result = flow.run_episode(ctx, config=config)
    assert result['passed'] is True and result['agent_seconds'] == 0
    assert result['attempts'] == []
    assert result['method'] == 'existing requests executed and accepted by host'
    assert [a for a in calls if a != 'status'] == ['check', 'render', 'finish']
    assert not (ctx.work_dir/'episode-session.json').exists()


@pytest.mark.parametrize('prior_status', ['finished_with_issues', 'running'])
def test_retry_of_started_episode_renders_current_plan_without_readopting_a_draft(ctx, config, monkeypatch, prior_status):
    ctx = replace(ctx, input_mode='script')
    install_fake_runtime(monkeypatch, ctx)
    atomic_write_json(ctx.run_dir/'agent-run.json', {'status': prior_status, 'passed': False,
                                                   'deadline': time.time()-1})
    original = flow.EpisodeTools
    payloads = []
    video=ctx.episode_dir/'work/clips/c/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True);video.write_bytes(b'existing take')
    class Tools(original):
        def dispatch(self, action, payload):
            if action == 'render':
                payloads.append(payload)
            result = super().dispatch(action, payload)
            if action == 'check': result['valid'] = True
            if action == 'render': result['quality_passed'] = True
            return result
    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    monkeypatch.setattr(episode_turns, 'run_session', lambda *a, **k: pytest.fail('cached completed media needs no draft rewrite'))
    result = flow.run_episode(ctx, config=config, retry_issues=True)
    assert result['passed'] and result['deadline'] > time.time()
    assert payloads == [{}]


@pytest.mark.parametrize('quality_passed', [False, None])
def test_host_failures_or_technical_only_results_reach_agent_with_existing_evidence(ctx, config, monkeypatch, quality_passed):
    ctx = replace(ctx, input_mode='script')
    calls = install_fake_runtime(monkeypatch, ctx)
    original = flow.EpisodeTools

    class Tools(original):
        def dispatch(self, action, payload):
            result = super().dispatch(action, payload)
            if action == 'check':
                result['valid'] = True
            elif action == 'render':
                result.update(technical_passed=True, quality_passed=quality_passed,
                              review={'evidence': 'input/latest-review.json', 'feedback': {'clip_01': 'needs repair'}})
            elif action == 'finish':
                manifest = read_json(ctx.work_dir/'input/latest-finish.json')
                atomic_write_json(ctx.work_dir/'input/latest-finish.json', {**manifest, 'passed': False})
                result['passed'] = False
            return result

    monkeypatch.setattr(flow, 'EpisodeTools', Tools)
    bridges = capture_bridge(monkeypatch)
    sessions = []

    def run(work, prompt, **options):
        sessions.append(options)
        assert calls.count('render') == 1
        snapshot = read_json(work/'input/current-status.json')
        assert snapshot['job']['result']['review']['feedback'] == {'clip_01': 'needs repair'}
        atomic_write_json(work/'episode-session.json', {'session_id': 'same-session'})
        bridges[0].handle('finish', {})
        return fake_attempt(ctx, 1)

    monkeypatch.setattr(episode_turns, 'run_session', run)
    result = flow.run_episode(ctx, config=config)
    assert len(sessions) == 1 and result['passed'] is False
    assert result['status'] == 'finished_with_issues'
    assert calls.count('render') == 1


def test_normal_tool_handoffs_can_exceed_three_invocations(ctx, config, monkeypatch):
    calls = install_fake_runtime(monkeypatch, ctx)
    bridges = capture_bridge(monkeypatch)
    actions = ['render', 'review', 'apply', 'render', 'review', 'finish']
    sessions = []

    def run(work, prompt, **options):
        sessions.append(options)
        snapshot = read_json(work/'input/current-status.json')
        assert snapshot['busy'] is False
        if len(sessions) == 1:
            assert snapshot['job'] is None
        else:
            assert snapshot['job']['action'] == actions[len(sessions) - 2]
            assert snapshot['job']['status'] == 'completed'
            assert snapshot['job']['result']['ok'] is True
        atomic_write_json(work / 'episode-session.json', {'session_id': 'same-session'})
        answer = bridges[0].handle(actions[len(sessions) - 1], {})
        assert answer['yield'] is True and answer['status'] == 'accepted'
        return fake_attempt(ctx, len(sessions))

    monkeypatch.setattr(episode_turns, 'run_session', run)
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

    monkeypatch.setattr(episode_turns, 'run_session', run)
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

    monkeypatch.setattr(episode_turns, 'run_session', run)
    paused = flow.run_episode(ctx, config=config)
    assert paused['status'] == 'paused' and paused['job']['status'] == 'accepted'
    assert calls.count('render') == 0
    deadline = paused['deadline']
    marker.unlink()
    finished = flow.run_episode(ctx, config=config)
    assert finished['passed'] and 0 <= finished['deadline']-deadline < 2
    assert calls.count('render') == 1 and calls.count('finish') == 1
    assert [row['resume'] for row in sessions] == [False, True]


def test_duplicate_executor_does_not_start_a_model_or_change_budget(ctx, config, monkeypatch):
    ctx.source_episode_dir.mkdir(parents=True, exist_ok=True)
    lock = ctx.source_episode_dir / '.render.lock'
    lock.write_text(str(os.getpid()))
    monkeypatch.setattr(episode_turns, 'run_session', lambda *a, **k: pytest.fail('duplicate model call'))
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

    monkeypatch.setattr(episode_turns, 'run_session', run)
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
    if started:
        receipt=ctx.episode_dir/'work/clips/c/attempt_01/clip.mp4.task.json'
        receipt.parent.mkdir(parents=True)
        atomic_write_json(receipt,{'task_id':'already submitted'})

    class Tools(original):

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

    monkeypatch.setattr(episode_turns, 'run_session', run)
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
    monkeypatch.setattr(episode_turns, 'run_session', lambda *a, **k: pytest.fail('finished session resumed'))
    monkeypatch.setattr(flow, 'EpisodeBridge', lambda *a: pytest.fail('finished tools restarted'))
    result = flow.run_episode(ctx, config=config)
    assert result['passed'] and result['status'] == 'finished'
    assert result['deadline'] < time.time()


def test_accepted_episode_resumes_delivery_without_agent_or_renewed_budget(ctx, config, monkeypatch):
    from novel_manga.application.production import modelscope_delivery
    calls = install_fake_runtime(monkeypatch, ctx)
    saved = {'status': 'finished', 'phase': 'finished', 'passed': True,
             'attempts': [1, 2, 3], 'deadline': time.time()-300, 'remaining_seconds': 0,
             'delivery': {'status': 'pending', 'reason': 'upload interrupted'}, 'exit_code': 2}
    atomic_write_json(ctx.run_dir/'agent-run.json', saved)
    atomic_write_json(ctx.source_novel_dir/'modelscope_delivery.json', {'repository': 'owner/book'})
    monkeypatch.setattr(episode_turns, 'run_session', lambda *a, **k: pytest.fail('accepted Agent resumed'))
    monkeypatch.setattr(flow, 'EpisodeBridge', lambda *a: pytest.fail('accepted media tools restarted'))
    uploads = []
    def upload(command, **options):
        from types import SimpleNamespace
        uploads.append(command)
        atomic_write_json(ctx.run_dir/'delivery/modelscope.json',
                          {'verified_remote': True, 'repository': 'owner/book'})
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(modelscope_delivery.subprocess, 'run', upload)
    result = flow.run_episode(ctx, config=config)
    assert result['passed'] and result['status'] == 'finished'
    assert result['attempts'] == saved['attempts'] and result['deadline'] == saved['deadline']
    assert result['remaining_seconds'] == 0 and calls == []
    assert len(uploads) == 1 and result['delivery']['verified_remote'] and result['exit_code'] == 0


def test_host_reviews_applied_crop_and_finishes_without_another_agent_or_render(ctx,config,monkeypatch):
    calls=install_fake_runtime(monkeypatch,ctx);bridges=capture_bridge(monkeypatch)
    original=flow.EpisodeTools
    class Tools(original):
        def dispatch(self,action,payload):
            result=super().dispatch(action,payload)
            if action=='apply':result.update(media_changed=['clip_01'],media_operation='crop',review_required=True)
            if action=='review':result.update(quality_passed=True)
            return result
    monkeypatch.setattr(flow,'EpisodeTools',Tools)
    sessions=[]
    def session(work,prompt,**options):
        sessions.append(1);assert len(sessions)==1
        atomic_write_json(work/'episode-session.json',{'session_id':'same-session'})
        bridges[-1].handle('apply',{})
        return fake_attempt(ctx,1)
    monkeypatch.setattr(episode_turns,'run_session',session)
    result=flow.run_episode(ctx,config=config)
    assert result['passed'] and sessions==[1]
    assert [a for a in calls if a!='status']==['apply','review','finish']


def test_pause_interrupts_waiting_for_model_without_invocation(ctx, config, monkeypatch):
    install_fake_runtime(monkeypatch, ctx)
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', json.dumps([{'base': config['base_url'], 'slots': 1}]))
    monkeypatch.setenv('NOVEL_MODEL_INFLIGHT_DIR', str(ctx.run_dir / 'slots'))
    marker = ctx.run_dir / 'pause'
    marker.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('NOVEL_AGENT_PAUSE_FILE', str(marker))
    monkeypatch.setattr(episode_turns, 'run_session', lambda *a, **k: pytest.fail('paused model called'))
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
    monkeypatch.setattr(episode_turns, 'run_session', lambda *a, **k: pytest.fail('settled finish resumed a model'))
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

    monkeypatch.setattr(episode_turns, 'run_session', run)
    result = flow.run_episode(ctx, config=config)
    assert result['passed'] is False and result['status'] == 'blocked'
    assert calls.count('finish') == 0



def test_newly_available_permission_is_progress_but_same_unused_permission_is_not(ctx):
    from novel_manga.application.agents.episode import progress as rules
    old = {'repair_actions':{'clip_01':[]}, 'remaining_takes':{'clip_01':0},
        'job':{'id':'job-1','action':'render','status':'completed','result':{'ok':False}}}
    new = {**old,'repair_actions':{'clip_01':['edit']},'remaining_takes':{'clip_01':1}}
    before = rules.progress(ctx,old)
    assert rules.made_progress(ctx,before,old,new)
    assert not rules.made_progress(ctx,rules.progress(ctx,new),new,new)


def test_new_input_route_is_progress_without_resetting_numeric_input_count(ctx):
    from novel_manga.application.agents.episode import progress as rules
    old = {'input_operations':{'clip_01':{'resume':{'allowed':False}}}}
    new = {'input_operations':{'clip_01':{'resume':{'allowed':True}}}}
    assert rules.made_progress(ctx,rules.progress(ctx,old),old,new)
    assert not rules.made_progress(ctx,rules.progress(ctx,new),new,new)
