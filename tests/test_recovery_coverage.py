"""Accepted repair intent must reach execution without restarting unrelated work."""
import copy
from types import SimpleNamespace
import pytest
from novel_manga.util import read_json, atomic_write_json
from novel_manga.application.agents.episode import upstream_assets, input_repair, recovery
from novel_manga.application.agents.episode.tools import EpisodeTools
from support.upstream_recovery import ctx_pair, approved_source, fake_image, row
from support.repack_budget import long_context
from novel_manga.application.production import recovery_coverage


def filmed(ctx,failed):
    plan=read_json(ctx.episode_dir/'clip_plan.json');clips=[]
    for c in plan['clips']:
        video=ctx.episode_dir/'work/clips'/c['clip_id']/'attempt_01/clip.mp4'
        video.parent.mkdir(parents=True,exist_ok=True);video.write_bytes(b'old take')
        clips.append({'clip_id':c['clip_id'],'selected':{'video':str(video),'passed':True}})
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',{'clips':clips})
    atomic_write_json(ctx.episode_dir/'episode_review.json',{'feedback':{cid:'confirmed defect'for cid in failed}})


def test_verified_asset_candidate_can_target_its_passed_seed_clip(ctx_pair,approved_source,fake_image,monkeypatch):
    ctx=ctx_pair[0];filmed(ctx,['clip_02'])
    candidate=upstream_assets.apply_to_snapshot(ctx,upstream_assets.prepare(ctx,row(clip_ids=['clip_01'])))
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[candidate['input_decision']]})
    monkeypatch.setattr(input_repair,'compile_input',lambda *a:SimpleNamespace(proposal=None))
    tools=EpisodeTools(ctx)
    assert tools.check({'file':'output/repair.json'})['valid']
    result=tools.dispatch('apply',{'file':'output/repair.json'})
    assert result['ok'] and result['input_repaired']==['clip_01']
    assert 'clip_02'not in result['blocked']
    assert not (ctx.run_dir/'final-candidate/manifest.json').exists()


def test_verified_input_can_be_compiled_with_zero_video_balance(ctx_pair,approved_source,fake_image,monkeypatch):
    from novel_manga.application.repair import history
    ctx=ctx_pair[0]
    candidate=upstream_assets.apply_to_snapshot(ctx,upstream_assets.prepare(ctx,row(clip_ids=['clip_01'])))
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json',{'*':{'limit':0,'reason':'spent video'}})
    before=(ctx.episode_dir/'repair_budget_grants.json').read_bytes()
    monkeypatch.setattr(input_repair,'compile_input',lambda *a:SimpleNamespace(proposal=None))
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[candidate['input_decision']]})
    tools=EpisodeTools(ctx)
    assert tools.check({'file':'output/repair.json'})['valid']
    result=tools.dispatch('apply',{'file':'output/repair.json'})
    assert result['ok'] and result['input_repaired']==['clip_01']
    assert history.remaining_takes(ctx.episode_dir,'clip_01')==0
    assert(ctx.episode_dir/'repair_budget_grants.json').read_bytes()==before


def test_unverified_passed_target_rejected_by_both_check_and_apply(ctx_pair,monkeypatch):
    ctx=ctx_pair[0];filmed(ctx,['clip_02'])
    change={'clip_id':'clip_01','action':'input','source_quote':'甲坐在宿舍','reason':'unverified change',
            'shots':[{'index':1,'light':'白天'}]}
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[change]})
    monkeypatch.setattr(input_repair,'source_check',lambda *a,**kw:pytest.fail('illegal target reached judge'))
    tools=EpisodeTools(ctx)
    assert not tools.check({'file':'output/repair.json'})['valid']
    assert not tools.dispatch('apply',{'file':'output/repair.json'})['ok']
    assert not list((ctx.episode_dir/'work/input_repairs').glob('*.json'))


def test_partial_retake_keeps_other_problem_pending_without_veto(ctx_pair):
    ctx=ctx_pair[0];filmed(ctx,['clip_01','clip_02'])
    old=read_json(ctx.episode_dir/'clip_plan.json')['clips'][1:]
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[
        {'clip_id':'clip_01','action':'retake','reason':'real defect, one permitted attempt'}]})
    tools=EpisodeTools(ctx)
    assert tools.check({'file':'output/repair.json'})['valid']
    result=tools.dispatch('apply',{'file':'output/repair.json'})
    assert result['ok'] and result['changed']==['clip_01']
    assert not result['blocked'] and 'clip_02'in result['unresolved_clips']
    assert 'pending'not in result
    assert read_json(ctx.episode_dir/'clip_plan.json')['clips'][1:]==old


def test_route_repack_includes_all_current_long_ranges(long_context):
    ctx,plan=long_context
    plan['clips'][1]['request_seconds']=20
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    route=recovery.upstream_route(ctx,{})
    assert route['kind']=='repack'
    assert route['decision']['clip_ids']==['clip_01','clip_02']


def test_finish_does_not_treat_old_omission_receipt_as_media_defect(ctx_pair,monkeypatch):
    ctx=ctx_pair[0];captured=[]
    atomic_write_json(ctx.run_dir/'tool-repair-decisions.json',[{'blocked':{
        'clip_01':'agent omitted a decision','clip_02':'confirmed unresolved source defect'}}])
    def save(directory,report,review,**kw):
        captured.append(kw['blocked'])
        return {**report,'quality_review':{'passed':False}}
    monkeypatch.setattr('novel_manga.application.review.execution.save_result',save)
    result=EpisodeTools(ctx).dispatch('finish',{})
    assert captured==[{'clip_02':'confirmed unresolved source defect'}]
    assert not result['passed']


def test_prepared_asset_is_adopted_before_any_redraw(ctx_pair,approved_source,fake_image):
    ctx=ctx_pair[0]
    upstream_assets.apply_to_snapshot(ctx,upstream_assets.prepare(ctx,row()))
    route=recovery_coverage.inspect_context(ctx)
    assert route['kind']=='verified_input' and route['decision']['action']=='input'
    assert fake_image.calls==1


def test_missing_plan_returns_to_native_pipeline_even_with_spent_agent_clock(ctx_pair):
    ctx=ctx_pair[0]
    (ctx.episode_dir/'clip_plan.json').unlink()
    atomic_write_json(ctx.run_dir/'agent-run.json',{'status':'blocked','remaining_seconds':0})
    assert recovery_coverage.inspect_context(ctx)['kind']=='native_plan'


def test_ordinary_repair_is_not_excluded_by_asset_only_routing(ctx_pair,monkeypatch):
    ctx=ctx_pair[0];filmed(ctx,['clip_01'])
    atomic_write_json(ctx.run_dir/'agent-run.json',{'status':'blocked','remaining_seconds':100})
    monkeypatch.setattr('novel_manga.application.profiles.h3_prompt_outdated',lambda *a,**kw:False)
    monkeypatch.setattr('novel_manga.story.h3.request_issues',lambda *a:[])
    monkeypatch.setattr('novel_manga.application.review.execution.media_matches',lambda *a,**kw:False)
    route=recovery_coverage.inspect_context(ctx)
    assert route['kind']=='agent_repair' and 'clip_01'in route['targets']


def test_spent_activity_remains_explicitly_blocked_without_new_evidence(ctx_pair,monkeypatch):
    ctx=ctx_pair[0];filmed(ctx,['clip_01'])
    atomic_write_json(ctx.run_dir/'agent-run.json',{'status':'blocked','remaining_seconds':0})
    monkeypatch.setattr('novel_manga.application.profiles.h3_prompt_outdated',lambda *a,**kw:False)
    monkeypatch.setattr('novel_manga.story.h3.request_issues',lambda *a:[])
    monkeypatch.setattr('novel_manga.application.review.execution.media_matches',lambda *a,**kw:False)
    route=recovery_coverage.inspect_context(ctx)
    assert route['kind']=='activity_exhausted'
    assert not(ctx.episode_dir/'repair_budget_grants.json').exists()


def test_preparation_failure_keeps_existing_takes_and_other_current_blocks(ctx_pair):
    from novel_manga.application.agents.episode.local_preparation import _blocked
    ctx=ctx_pair[0];filmed(ctx,['clip_01'])
    before=read_json(ctx.episode_dir/'thin_media_report.json')
    before['blocked_clips']={'clip_02':['actual old defect']}
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',before)
    _blocked(ctx,{'clip_01':'new input conflict'})
    after=read_json(ctx.episode_dir/'thin_media_report.json')
    assert after['clips']==before['clips']
    assert after['blocked_clips']['clip_02']==['actual old defect']


def test_changed_delivery_opens_only_actual_changed_clips(ctx_pair):
    from novel_manga.application.agents.episode.repair_targets import eligible_targets
    from novel_manga.application.profiles import plan_fingerprint
    ctx=ctx_pair[0];filmed(ctx,[])
    before=read_json(ctx.episode_dir/'clip_plan.json')
    atomic_write_json(ctx.run_dir/'final-candidate/clip_plan.json',before)
    report=read_json(ctx.episode_dir/'thin_media_report.json')
    report['clip_plan_fingerprint']=plan_fingerprint(before)
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',report)
    after=copy.deepcopy(before);after['clips'][0]['prompt']='changed current shot'
    atomic_write_json(ctx.episode_dir/'clip_plan.json',after)
    assert eligible_targets(ctx)=={'clip_01'}
    from novel_manga.application.profiles import h3_prompt_fingerprint
    report.update(clip_plan_fingerprint=plan_fingerprint(after),prompt_h3_fingerprint=h3_prompt_fingerprint(after),review_feedback={})
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',report)
    assert eligible_targets(ctx)==set()


def test_native_first_pass_does_not_renew_spent_agent_time(ctx_pair,monkeypatch,tmp_path):
    import os
    from contextlib import nullcontext
    from novel_manga.application.production import upstream_batch
    from novel_manga.application.agents.episode import lifecycle
    ctx=ctx_pair[0];calls=[]
    monkeypatch.setenv('NOVEL_PROJECT_ROOT',str(tmp_path))
    monkeypatch.setattr(recovery_coverage,'context',lambda *a:ctx)
    monkeypatch.setattr(lifecycle,'episode_lock',lambda *a:nullcontext())
    monkeypatch.setattr('novel_manga.application.agents.episode.inputs.prepare_inputs',lambda *a:None)
    monkeypatch.setattr('novel_manga.application.resolved_settings.resolve_agent_environment',lambda *a:{})
    monkeypatch.setattr('novel_manga.application.agents.sandbox.load_config',lambda *a:{})
    monkeypatch.setattr('novel_manga.application.agents.episode.local_preparation.ensure_requests',lambda *a:{})
    monkeypatch.setattr('novel_manga.application.agents.episode.flow.run_episode',lambda *a,**kw:pytest.fail('spent Agent clock renewed'))
    class Tools:
        def __init__(self,*a):pass
        def dispatch(self,action,*a):
            calls.append(action)
            if action=='render':assert os.environ['NOVEL_FIRST_PASS_ONLY']=='1'
            return {'ok':False,'passed':False,'error':'real defect remains'}
    monkeypatch.setattr('novel_manga.application.agents.episode.tools.EpisodeTools',Tools)
    old={'status':'blocked','remaining_seconds':0,'attempts':['old']}
    atomic_write_json(ctx.run_dir/'agent-run.json',old);out=tmp_path/'results';out.mkdir()
    result=recovery_coverage.execute_standard({'episode':7,'config':'unused','upstream_action':{'kind':'native_first_pass'}},ctx.novel_dir,out)
    assert calls==['render','review','finish'] and not result['passed']
    assert read_json(ctx.run_dir/'agent-run.json')==old


@pytest.mark.parametrize('reason',['Agent slice completed; resume this session after other production work',
                                 'Agent reached this slice time limit; continue the saved session'])
def test_both_deferred_slice_receipts_can_continue_with_existing_time(reason):
    assert recovery.conversation_deferred({'status':'deferred','phase':'deferred','reason':reason})


def test_finished_agent_cannot_return_old_success_for_changed_delivery(ctx_pair,monkeypatch):
    import time
    from novel_manga.application.agents.episode import lifecycle
    ctx=ctx_pair[0]
    saved={'status':'finished','passed':True,'remaining_seconds':100,'deadline':time.time()+100,
           'updated_at':time.strftime('%F %T'),'attempts':['old']}
    atomic_write_json(ctx.run_dir/'agent-run.json',saved)
    monkeypatch.setattr(lifecycle,'resolve_agent_environment',lambda *a:{})
    monkeypatch.setattr(lifecycle,'prepare_inputs',lambda *a:None)
    monkeypatch.setattr('novel_manga.application.agents.episode.local_preparation.stage',lambda *a:None)
    monkeypatch.setattr(lifecycle,'install_client',lambda *a:None)
    monkeypatch.setattr(lifecycle,'paused',lambda:True)
    monkeypatch.setattr('novel_manga.application.production.runs.episode_status',lambda *a:'stale')
    result=lifecycle.start_run(ctx,config={},timeout=100,retry_issues=True,tools_factory=lambda *a:None)
    assert result.state is not None and result.state.result['passed']is False
    assert result.state.result['attempts']==['old']


def test_uncaught_worker_error_has_episode_receipt_without_private_values(tmp_path,monkeypatch):
    from novel_manga.application.production import upstream_batch
    monkeypatch.setenv('NOVEL_PROJECT_ROOT',str(tmp_path))
    inventory=tmp_path/'inventory.json';out=tmp_path/'results'
    atomic_write_json(inventory,{'episodes':[{'episode':7,'run_dir':'original-run',
        'upstream_action':{'kind':'native_plan'}}]})
    def fail(*a):raise ValueError('private diagnostic value must not be echoed')
    monkeypatch.setattr(upstream_batch,'execute_row',fail)
    code=upstream_batch.main(['--inventory',str(inventory),'--novel-dir',str(tmp_path),
        '--result-dir',str(out),'--project-root',str(tmp_path),'--one','7'])
    r=read_json(out/'7.json')
    assert code==1 and r['status']=='failed' and r['passed']is False
    assert r['code_frames'] and 'private diagnostic value'not in str(r)


def test_native_planning_refreshes_registered_source_index_and_preserves_art(ctx_pair):
    from novel_manga.application.agents.episode.inputs import sync_source_index
    ctx=ctx_pair[0]
    atomic_write_json(ctx.source_novel_dir/'novel.json',{'source':'book.txt','chapters':[
        {'index':7,'title':'same chapter','start':120,'end':160},
        {'index':8,'title':'neighbour','start':160,'end':200}]})
    local={'source':'book.txt','chapters':[{'index':7,'title':'same chapter','start':100,'end':140}],
           'private_design':'keep','profile':{'style':'existing'}}
    atomic_write_json(ctx.novel_dir/'novel.json',local)
    assert sync_source_index(ctx)
    updated=read_json(ctx.novel_dir/'novel.json')
    assert updated['chapters'][0]['start']==120
    assert updated['chapters'][1]['index']==8
    assert updated['private_design']=='keep' and updated['profile']==local['profile']
    assert not sync_source_index(ctx)


def test_stale_source_segments_restore_registered_citations_without_changing_takes(ctx_pair):
    from novel_manga.application.agents.episode.inputs import sync_source_segments
    ctx=ctx_pair[0];filmed(ctx,['clip_01'])
    source='本章原文。甲坐在宿舍。'
    old=[{'segment_id':'seg_1','text':'上一章的内容'}]
    registered=[{'segment_id':'seg_1','text':source}]
    atomic_write_json(ctx.source_episode_dir/'segments.json',registered)
    atomic_write_json(ctx.episode_dir/'segments.json',old)
    before={name:(ctx.episode_dir/name).read_bytes()for name in ('chapter_script.json','clip_plan.json','thin_media_report.json')}
    assert sync_source_segments(ctx,old,source)==registered
    assert read_json(ctx.run_dir/'source-segments-before.json')==old
    assert before=={name:(ctx.episode_dir/name).read_bytes()for name in before}


def test_changed_citation_ids_are_not_reassigned_to_filmed_shots(ctx_pair):
    from novel_manga.application.agents.episode.inputs import sync_source_segments
    ctx=ctx_pair[0]
    old=[{'segment_id':'seg_1','text':'不属于本章'}]
    atomic_write_json(ctx.source_episode_dir/'segments.json',[{'segment_id':'different_id','text':'原文'}])
    assert sync_source_segments(ctx,old,'原文')==old
