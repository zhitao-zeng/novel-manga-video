from pathlib import Path
import copy
import pytest
from novel_manga.util import atomic_write_json,read_json
from novel_manga.application.repair import history
from novel_manga.application.agents.episode import bounded_recovery,upstream_assets,input_repair
from support.upstream_recovery import ctx_pair,approved_source,fake_image,row


@pytest.fixture
def ctx(ctx_pair):return ctx_pair[0]


@pytest.fixture
def approved_asset_evidence(ctx,approved_source,fake_image):
    result=upstream_assets.prepare(ctx,row());upstream_assets.apply_to_snapshot(ctx,result)
    return bounded_recovery.evidence(ctx,'clip_01')


def test_input_recovery_does_not_grant_video(ctx,approved_asset_evidence):
    before=history.generation_limit(ctx.episode_dir,'clip_01')
    first=bounded_recovery.grant(ctx,'clip_01',approved_asset_evidence,kind='input')
    assert first==bounded_recovery.grant(ctx,'clip_01',approved_asset_evidence,kind='input')
    assert history.generation_limit(ctx.episode_dir,'clip_01')==before
    assert not history.conditional_extra(ctx.episode_dir,'clip_01')


def test_self_reported_verified_cannot_grant(ctx):
    with pytest.raises(ValueError,match='evidence'):
        bounded_recovery.grant(ctx,'clip_01',{'verified':True,'reason':'model says fixed'},kind='input')
    assert not (ctx.episode_dir/'repair_budget_grants.json').exists()


def test_spent_recovery_remains_spent_after_restart(ctx,approved_asset_evidence):
    candidate=approved_asset_evidence['candidate']
    bounded_recovery.grant(ctx,'clip_01',approved_asset_evidence,kind='input')
    assert bounded_recovery.input_operation(ctx,'clip_01',candidate)['allowed']
    bounded_recovery.input_operation(ctx,'clip_01',candidate,consume=True)
    assert not bounded_recovery.input_operation(ctx,'clip_01',candidate)['allowed']
    bounded_recovery.grant(ctx,'clip_01',approved_asset_evidence,kind='input')
    assert not bounded_recovery.input_operation(ctx,'clip_01',candidate)['allowed']


def test_stale_candidate_cannot_consume_newer_evidence(ctx,approved_asset_evidence):
    bounded_recovery.grant(ctx,'clip_01',approved_asset_evidence,kind='input')
    bad=copy.deepcopy(approved_asset_evidence['candidate']);bad['phases']['characters']['甲']={'gender':'女'}
    assert not bounded_recovery.input_operation(ctx,'clip_01',bad)['allowed']


@pytest.mark.parametrize('has_plan,seconds',[(True,600),(False,1200)])
def test_local_window_is_600_source_window_is_1200(ctx,approved_asset_evidence,has_plan,seconds):
    bounded_recovery.grant(ctx,'clip_01',approved_asset_evidence,kind='time')
    if not has_plan:(ctx.episode_dir/'clip_plan.json').unlink()
    saved={'remaining_seconds':0,'status':'blocked','agent_seconds':123,'attempts':['old'],'input_mode':'source'}
    result=bounded_recovery.resume_window(ctx,saved)
    assert result['remaining_seconds']==seconds and result['agent_seconds']==123
    assert result['attempts']==['old']
    assert bounded_recovery.resume_window(ctx,result) is None


def test_resource_wait_does_not_use_window(ctx,approved_asset_evidence,monkeypatch):
    from novel_manga.application.agents.episode.lifecycle import EpisodeRunState
    bounded_recovery.grant(ctx,'clip_01',approved_asset_evidence,kind='time')
    resumed=bounded_recovery.resume_window(ctx,{'remaining_seconds':0,'status':'blocked','agent_seconds':99})
    atomic_write_json(ctx.run_dir/'agent-run.json',resumed)
    bounded_recovery.record_proposal(ctx)
    current=read_json(ctx.run_dir/'agent-run.json')
    assert current['upstream_recovery']['proposals']==1
    from types import SimpleNamespace
    state=SimpleNamespace(ctx=ctx,saved={'resource_wait_seconds':0},result={'deadline':1000},
        deadline=1000,wait_started=0,wait_credited=0)
    monkeypatch.setattr('novel_manga.resource_waits.wait_seconds',lambda *a,**k:180)
    EpisodeRunState.account_resources(state)
    assert state.deadline==1180 and state.result['deadline']==1180
    EpisodeRunState.account_resources(state)
    assert state.deadline==1180
    assert current['agent_seconds']==99


def test_two_proposals_stop_without_losing_host_receipt(ctx,approved_asset_evidence):
    bounded_recovery.grant(ctx,'clip_01',approved_asset_evidence,kind='time')
    resumed=bounded_recovery.resume_window(ctx,{'remaining_seconds':0,'status':'blocked'})
    resumed['job']={'id':'job-8','action':'apply','status':'completed','result':{'changed':['clip_01']}}
    atomic_write_json(ctx.run_dir/'agent-run.json',resumed)
    bounded_recovery.record_proposal(ctx);bounded_recovery.record_proposal(ctx)
    with pytest.raises(ValueError,match='two|proposal'):
        bounded_recovery.record_proposal(ctx)
    assert read_json(ctx.run_dir/'agent-run.json')['job']==resumed['job']


def test_old_input_revision_resume_and_voice_counts_are_preserved(ctx,approved_asset_evidence):
    p=ctx.episode_dir/'work/input_repairs/clip_01-2.json'
    old={'clip_id':'clip_01','status':'incomplete','step':'compile','revision_count':1,'resume_count':1,
         'decision':{'clip_id':'clip_01','action':'input','reason':'old','source_quote':'甲坐在宿舍',
                     'shots':[{'index':1,'light':'夜晚'}]}}
    atomic_write_json(p,old);voice=ctx.episode_dir/'work/inner_voice/clip_01/voice.json'
    atomic_write_json(voice,{'generated_voice_takes':3});before=p.read_bytes()
    bounded_recovery.grant(ctx,'clip_01',approved_asset_evidence,kind='input')
    receipt,record=input_repair.input_receipt(ctx,'clip_01',approved_asset_evidence['candidate'])
    assert receipt!=p and p.read_bytes()==before
    assert record['upstream_recovery']
    assert read_json(voice)['generated_voice_takes']==3


def test_existing_unused_extra_is_reused(ctx,approved_asset_evidence):
    history.save(ctx.episode_dir,{'prior_generation_counts':{'clips':{'clip_01':3}},'trials':[]})
    first=history.grant_conditional_extra(ctx.episode_dir,'clip_01',authorization='old-extra',reason='verified')
    bounded_recovery.grant(ctx,'clip_01',approved_asset_evidence,kind='input')
    assert history.conditional_extra(ctx.episode_dir,'clip_01')==first


def test_used_extra_is_not_reissued(ctx,approved_asset_evidence):
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json',{'clip_01':{'limit':4,'reason':'old',
        'conditional_extra':{'limit':4,'authorization':'used','reason':'one extra','prepared':True,'activated':True}}})
    before=(ctx.episode_dir/'repair_budget_grants.json').read_bytes()
    bounded_recovery.grant(ctx,'clip_01',approved_asset_evidence,kind='time')
    assert read_json(ctx.episode_dir/'repair_budget_grants.json')['clip_01']['conditional_extra']['authorization']=='used'
    assert history.generation_limit(ctx.episode_dir,'clip_01')==4
