"""Reproductions of the independent whole-branch review, using real receipts."""
import copy
import time
from types import SimpleNamespace
import pytest
from novel_manga.util import atomic_write_json, read_json
from novel_manga.application.agents.episode import upstream_assets, input_repair, lifecycle
from novel_manga.application.production import upstream_batch
from novel_manga.application.repair import history
from support.upstream_recovery import ctx_pair, approved_source, fake_image, row
from support.repack_budget import long_context, repacked_episode, output, decision
from support.generation_receipts import episode, runner, completed_provider, task


def test_shared_asset_preparation_with_real_frozen_context(ctx_pair,approved_source,fake_image):
    from dataclasses import fields
    from novel_manga.application.agents.episode.context import EpisodeContext
    sample=ctx_pair[0]
    ctx=EpisodeContext(**{f.name:getattr(sample,f.name) for f in fields(EpisodeContext)})
    result=upstream_assets.prepare(ctx,row())
    assert (ctx.source_novel_dir/result['asset']['path']).is_file()


def test_legacy_unindexed_script_uses_its_recorded_shot_addresses(ctx_pair,approved_source,fake_image):
    ctx=ctx_pair[0];script=read_json(ctx.episode_dir/'chapter_script.json')
    for shot in script['shots']:shot.pop('index',None)
    atomic_write_json(ctx.episode_dir/'chapter_script.json',script)
    assert upstream_assets.prepare(ctx,row())['ok']


def test_completed_parent_task_can_be_repacked(long_context):
    from novel_manga.application.agents.episode import scoped_repack
    ctx, _ = long_context
    video = output(ctx.episode_dir, 'clip_01'); video.write_bytes(b'completed video')
    task(video, 'completed-parent')
    assert scoped_repack.apply(ctx, decision())['ok']
    assert video.read_bytes() == b'completed video'


def test_task_resume_keeps_reservation_during_admission(episode, monkeypatch):
    from novel_manga.media import generation
    from novel_manga.application.rendering import submission
    d, clip, _ = episode
    state = history.load(d)
    state['trials'][0]['changes'] = {'groups':[{'old':['c'], 'new':['c','sibling'],
        'source_budget':{'roots':{'c':{'limit':1,'used':0}},
            'children':{'c':{'roots':['c'],'baseline_count':0},
                        'sibling':{'roots':['c'],'baseline_count':0}}}}]}
    history.save(d, state)
    r = runner(d, clip, completed_provider([], 'pending-task'))
    video = output(d, 'c')
    request = generation.build_request(r.context, clip, 1)[0]
    reserved = history.reserve_scope_submission(d, video, {**request,'clip_id':'c'})
    task(video, 'pending-task')
    def admission(*args):
        assert read_json(video.parent/'request.json')['_repack_budget'] == reserved['_repack_budget']
        assert history.remaining_takes(d, 'sibling') == 0
        with pytest.raises(ValueError):
            history.reserve_scope_submission(d, output(d,'sibling'), {'clip_id':'sibling','prompt':'new'})
        return object()
    monkeypatch.setattr(submission, 'acquire_inflight_slot', admission)
    assert r.generate_clip(clip, 1).is_file()


def test_input_receipt_never_overwrites_asset_evidence(ctx_pair, approved_source, fake_image):
    ctx = ctx_pair[0]
    candidate = upstream_assets.apply_to_snapshot(ctx, upstream_assets.prepare(ctx,row()))
    from pathlib import Path
    evidence = Path(candidate['evidence_path']); original = evidence.read_bytes()
    receipt, _ = input_repair.input_receipt(ctx, 'clip_01', candidate['input_decision'])
    assert receipt != evidence
    assert evidence.read_bytes() == original


def test_selected_phase_design_survives_single_field_repair(ctx_pair):
    ctx = ctx_pair[0]
    atomic_write_json(ctx.novel_dir/'series_assets/phases.json', {'characters':{'甲':[
        {'from':1,'to':99,'asset_id':'a','age':'孩童','appearance':'白发','wardrobe':'黑袍'}]}})
    checked = upstream_assets.validate(ctx, row())
    assert checked['design']['gender'] == '男'
    assert {k:checked['design'][k] for k in ('age','appearance','wardrobe')} == {
        'age':'孩童','appearance':'白发','wardrobe':'黑袍'}


def test_location_adoption_reuses_exact_reviewed_card(ctx_pair, fake_image, monkeypatch):
    ctx = ctx_pair[0]
    monkeypatch.setattr(upstream_assets,'ask_json',lambda *a,**kw:{'kind':'image','reason':'地点卡错误',
        'source_quote':'甲坐在宿舍','replacement_asset_id':'','binding_shots':[]})
    monkeypatch.setattr(input_repair,'source_check',lambda *a,**kw:{'consistent':True,'problems':[]})
    plan = read_json(ctx.episode_dir/'clip_plan.json')
    ref = next(r for r in plan['clips'][0]['references'] if r['role']=='location')
    (ctx.run_dir/'source.txt').write_text('甲坐在宿舍。')
    proposal = row(asset_id=ref['asset_id'],source_quote='甲坐在宿舍',fields={'description':'石墙房间'})
    adopted = upstream_assets.apply_to_snapshot(ctx,upstream_assets.prepare(ctx,proposal))
    # Run actual source compilation; stop only external translation and model review.
    monkeypatch.setattr('novel_manga.application.agents.episode.input_assets.build_locations',
        lambda ctx,names: None if not names else pytest.fail('reviewed location was redrawn'))
    def compile(case,receipt,record,progress):
        phase = read_json(ctx.novel_dir/'series_assets/phases.json')['locations'][ref['name']][0]
        assert phase['asset_id'] == adopted['asset']['asset_id']
        assert (ctx.novel_dir/'series_assets/locations'/phase['asset_id']/'establishing.jpeg').is_file()
        return SimpleNamespace(proposal=None)
    monkeypatch.setattr(input_repair,'compile_input',compile)
    input_repair.repair(ctx,adopted['input_decision'])
    assert fake_image.calls == 1


@pytest.mark.parametrize('status',['running','retrying'])
def test_interrupted_recovery_retry_does_not_renew_deadline(ctx_pair,monkeypatch,status):
    ctx = ctx_pair[0]
    deadline = time.time()-10
    saved = {'status':status,'deadline':deadline,'remaining_seconds':0,
             'upstream_recovery':{'seconds':600,'proposals':1},'attempts':[]}
    atomic_write_json(ctx.run_dir/'agent-run.json',saved)
    monkeypatch.setattr(lifecycle,'resolve_agent_environment',lambda *a:{})
    monkeypatch.setattr(lifecycle,'prepare_inputs',lambda *a:None)
    monkeypatch.setattr('novel_manga.application.agents.episode.local_preparation.stage',lambda *a:None)
    monkeypatch.setattr(lifecycle,'install_client',lambda *a:None)
    monkeypatch.setattr(lifecycle,'paused',lambda:True)
    result = lifecycle.start_run(ctx,config={},timeout=600,retry_issues=True,tools_factory=lambda *a:None)
    current = result.completed or result.state.saved
    assert current['deadline'] == deadline
    assert current['upstream_recovery']['proposals'] == 1


def test_failed_asset_without_window_not_reselected(ctx_pair,approved_source,fake_image,monkeypatch):
    ctx = ctx_pair[0]
    atomic_write_json(ctx.run_dir/'agent-run.json',{'status':'blocked','remaining_seconds':0})
    atomic_write_json(ctx.episode_dir/'work/input_repairs/clip_01-1.json',{'clip_id':'clip_01',
        'status':'incomplete','decision':{'clip_id':'clip_01','action':'input','reason':'卡面有误',
        'source_quote':'甲是个男人。','phases':{'characters':{'甲':{'gender':'男'}}}}})
    monkeypatch.setattr(upstream_assets,'ask_json',lambda *a,**kw:{'kind':'uncertain','reason':'原文无法确定',
        'source_quote':'甲是个男人。','replacement_asset_id':'','binding_shots':[]})
    upstream_assets.prepare(ctx,row())
    monkeypatch.setattr(upstream_batch,'context',lambda *a:ctx)
    current = upstream_batch.inspect_row({'episode':7},ctx.novel_dir)
    assert current['recovery_failed']
    picked,held = upstream_batch.eligible_rows([{'episode':7}],lambda r:current)
    assert not picked and held


def test_batch_triage_does_not_recompute_unneeded_full_episode_status(ctx_pair,monkeypatch):
    from novel_manga.application.agents.episode.tools import EpisodeTools
    ctx=ctx_pair[0]
    monkeypatch.setattr(upstream_batch,'context',lambda *a:ctx)
    monkeypatch.setattr(EpisodeTools,'status',lambda *a:pytest.fail('batch selection needs saved upstream evidence only'))
    assert upstream_batch.inspect_row({'episode':7},ctx.novel_dir)['upstream_action'] is None


def test_batch_persists_successful_change_for_native_render_handoff(ctx_pair,monkeypatch,tmp_path):
    from contextlib import nullcontext
    ctx=ctx_pair[0]
    monkeypatch.setenv('NOVEL_PROJECT_ROOT',str(tmp_path))
    monkeypatch.setattr(upstream_batch,'context',lambda *a:ctx)
    monkeypatch.setattr(lifecycle,'episode_lock',lambda *a:nullcontext())
    monkeypatch.setattr('novel_manga.application.agents.episode.inputs.prepare_inputs',lambda *a:None)
    monkeypatch.setattr('novel_manga.application.resolved_settings.resolve_agent_environment',lambda *a:{})
    monkeypatch.setattr('novel_manga.application.agents.sandbox.load_config',lambda *a:{})
    monkeypatch.setattr('novel_manga.application.agents.episode.pipeline_preparation.prepare_repacked',lambda *a:{'ready':True})
    class Tools:
        def __init__(self,*a):pass
        def check(self,*a):return {'valid':True}
        def dispatch(self,*a):return {'ok':True,'repacked':True,'changed':['clip_01']}
    monkeypatch.setattr('novel_manga.application.agents.episode.tools.EpisodeTools',Tools)
    atomic_write_json(ctx.run_dir/'agent-run.json',{'status':'blocked','remaining_seconds':300})
    calls=[]
    def native(ctx,**kw):
        saved=read_json(ctx.run_dir/'agent-run.json');job=saved['job']
        assert job['action']=='apply' and job['status']=='completed'
        assert job['result']['changed']==['clip_01'] and job['result']['repacked']
        calls.append(kw)
        if len(calls)==1:
            saved.update(status='deferred',phase='deferred',remaining_seconds=250,
                reason='Agent slice completed; resume this session after other production work')
            atomic_write_json(ctx.run_dir/'agent-run.json',saved)
            return saved
        return {'status':'finished_with_issues','passed':False}
    monkeypatch.setattr('novel_manga.application.agents.episode.flow.run_episode',native)
    out=tmp_path/'results';out.mkdir()
    upstream_batch.execute_row({'episode':7,'config':'unused','upstream_action':{'kind':'repack',
        'decision':{'clip_id':'clip_01','action':'repack'}}},ctx.novel_dir,out)
    assert len(calls)==2 and calls[1]['timeout']==250 and not calls[1]['retry_issues']
