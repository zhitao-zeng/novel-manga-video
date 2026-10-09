"""Public host operations and original-clock recovery handoff."""
import pytest
from novel_manga.util import atomic_write_json,read_json
from novel_manga.application.agents.episode import recovery,upstream_assets,bounded_recovery
from novel_manga.application.agents.episode.tools import EpisodeTools
from support.upstream_recovery import ctx_pair,approved_source,fake_image,row


def test_candidate_only_is_not_episode_accepted(ctx_pair,approved_source,fake_image):
    ctx=ctx_pair[0];atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[row()]})
    checked=EpisodeTools(ctx).check({'file':'output/repair.json'})
    assert checked['valid'] and fake_image.calls==0
    result=EpisodeTools(ctx).dispatch('apply',{'file':'output/repair.json'})
    assert result['ok'] and result['upstream_action']=='repair_assets'
    assert 'input_decision'in result and not result.get('passed')
    assert not (ctx.run_dir/'final-candidate/manifest.json').exists()


def test_no_video_permission_still_allows_asset_diagnosis(ctx_pair,approved_source,fake_image):
    ctx=ctx_pair[0];atomic_write_json(ctx.episode_dir/'repair_budget_grants.json',{'*':{'limit':0,'reason':'spent'}})
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',{'blocked_clips':{'clip_01':['card conflict']}})
    status=EpisodeTools(ctx).status({})
    assert 'repair_assets'in status['repair_actions']['clip_01']
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[row()]})
    assert EpisodeTools(ctx).check({'file':'output/repair.json'})['valid']
    assert EpisodeTools(ctx).dispatch('apply',{'file':'output/repair.json'})['ok']
    assert fake_image.calls==1


def test_same_failed_recovery_is_not_queued_again(ctx_pair,approved_source,fake_image):
    ctx=ctx_pair[0];prepared=upstream_assets.prepare(ctx,row());upstream_assets.apply_to_snapshot(ctx,prepared)
    proof=bounded_recovery.evidence(ctx,'clip_01');bounded_recovery.grant(ctx,'clip_01',proof,kind='input')
    bounded_recovery.input_operation(ctx,'clip_01',proof['candidate'],consume=True)
    status=EpisodeTools(ctx).status({});route=recovery.upstream_route(ctx,status)
    assert route['kind']!='input_recovery'


def test_uncertain_source_is_reported_without_redraw(ctx_pair,approved_source,fake_image,monkeypatch):
    ctx=ctx_pair[0];monkeypatch.setattr(upstream_assets,'ask_json',lambda *a,**kw:{'kind':'uncertain',
        'reason':'不能确认原文身份','source_quote':'甲是个男人。','replacement_asset_id':'','binding_shots':[]})
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[row()]})
    result=EpisodeTools(ctx).dispatch('apply',{'file':'output/repair.json'})
    assert result['ok']is False and result['kind']=='uncertain' and fake_image.calls==0


def test_source_generation_stays_native_not_agent_authoring(ctx_pair):
    ctx=ctx_pair[0];status=EpisodeTools(ctx).status({})
    route=recovery.upstream_route(ctx,status)
    assert route['kind']in {'episode_repair','needs_evidence'}
    assert not route.get('author_episode')


def test_repack_can_be_requested_through_check_without_touching_plan(ctx_pair):
    ctx=ctx_pair[0];plan=read_json(ctx.episode_dir/'clip_plan.json');plan['clips'][0]['request_seconds']=28
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan);before=(ctx.episode_dir/'clip_plan.json').read_bytes()
    proposal={'clip_id':'clip_01','action':'repack','reason':'超过当前上限','source_quote':'甲坐在宿舍'}
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[proposal]})
    checked=EpisodeTools(ctx).check({'file':'output/repair.json'})
    assert checked['valid'] and (ctx.episode_dir/'clip_plan.json').read_bytes()==before


def test_finished_host_job_is_saved_after_proposal_limit(ctx_pair,approved_source,fake_image):
    from novel_manga.application.agents.episode import flow
    ctx=ctx_pair[0];prepared=upstream_assets.prepare(ctx,row());upstream_assets.apply_to_snapshot(ctx,prepared)
    bounded_recovery.grant(ctx,'clip_01',bounded_recovery.evidence(ctx,'clip_01'),kind='time')
    result=bounded_recovery.resume_window(ctx,{'status':'blocked','remaining_seconds':0})
    result['upstream_recovery']['proposals']=2
    result['job']={'id':'job-4','status':'completed','action':'finish','result':{'passed':False}}
    atomic_write_json(ctx.run_dir/'agent-run.json',result)
    with pytest.raises(ValueError):bounded_recovery.record_proposal(ctx)
    assert read_json(ctx.run_dir/'agent-run.json')['job']['id']=='job-4'


def test_assets_recovery_hands_back_to_pipeline_then_formal_review(ctx_pair,approved_source,fake_image,monkeypatch):
    """Actual source compiler and host adoption; external translation/review simulated."""
    import shutil
    from novel_manga.application.profiles import h3_stamp
    ctx=ctx_pair[0];plan=read_json(ctx.episode_dir/'clip_plan.json')
    shutil.copy2(ctx.novel_dir/'cards/a.jpeg',ctx.novel_dir/'cards/a-good.jpeg')
    for clip in plan['clips'][1:]:
        for ref in clip['references']:
            if ref.get('asset_id')=='a':ref.update(asset_id='a-good',path='cards/a-good.jpeg')
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    old_good=plan['clips'][1:]
    for source,relative in [('cards/b.jpeg','series_assets/characters/character_002/turnaround.jpeg'),('cards/room.jpeg','series_assets/locations/location_001/establishing.jpeg')]:
        dest=ctx.novel_dir/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ctx.novel_dir/source,dest)
    def translated(clip,**kwargs):
        from novel_manga.application.agents.episode.requests import compose_request
        shots=[]
        for stage in range(1,int(clip.get('stage_count')or len(clip['shot_indexes']))+1):
            lines=[r for r in clip.get('dialogue_bindings',[])if r['stage']==stage]
            shots.append('The person remains in the room. '+ ' '.join(f'<DIALOGUE_{i}>'for i in range(1,len(lines)+1)))
        clip['prompt_h3']=compose_request(clip,{'shots':shots,'soundscape':'Natural room sounds.'})
        clip['prompt_h3_of']=h3_stamp(clip,'')
    monkeypatch.setattr('novel_manga.application.rendering.h3.convert',translated)
    monkeypatch.setattr('novel_manga.application.preparation.request_check.request_consistency',lambda *a,**k:{'consistent':True,'problems':[]})
    monkeypatch.setattr('novel_manga.application.preparation.wearer_cards.check_references',lambda *a:None)
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[row()]})
    tools=EpisodeTools(ctx);prepared=tools.dispatch('apply',{'file':'output/repair.json'})
    assert prepared['ok'] and prepared['input_decision']['clip_ids']==['clip_01']
    atomic_write_json(ctx.work_dir/'output/input-patch.json',{'decisions':[prepared['input_decision']]})
    assert tools.check({'file':'output/input-patch.json'})['valid']
    patched=tools.dispatch('apply',{'file':'output/input-patch.json'})
    assert patched['ok'] and patched['changed']==['clip_01']
    assert read_json(ctx.episode_dir/'clip_plan.json')['clips'][1:]==old_good
    # A source candidate must still fail formal finish without reviewed current takes.
    final=tools.dispatch('finish',{})
    assert not final.get('passed')
