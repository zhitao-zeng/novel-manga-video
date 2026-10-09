"""Source limits survive recuts; pending submissions share the actual balance."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import copy
import pytest
from novel_manga.util import atomic_write_json, read_json
from novel_manga.application.repair import history
from novel_manga.application.agents.episode import scoped_repack
from support.split_episode import split_episode


@pytest.fixture
def repacked_episode(tmp_path):
    d=tmp_path/'book_1';d.mkdir()
    atomic_write_json(d/'clip_plan.json',{'clips':[{'clip_id':cid,'kind':'video'} for cid in ['clip_01','clip_06','clip_99']]})
    group={'old':['clip_01'],'new':['clip_01','clip_06'],
        'source_budget':{'roots':{'clip_01':{'limit':3,'used':1}},
            'children':{'clip_01':{'roots':['clip_01'],'baseline_count':1},
                        'clip_06':{'roots':['clip_01'],'baseline_count':0}}}}
    history.save(d,{'prior_generation_counts':{'clips':{'clip_01':1}},
                    'trials':[{'id':1,'method':'agent_scoped_repack','changes':{'groups':[group]}}]})
    return d


def test_two_children_share_parent_balance(repacked_episode):
    d=repacked_episode
    assert history.generation_counts(d)['clip_01']==1
    assert history.generation_scope(d,'clip_06')=={'clip_01','clip_06'}
    budget=history.source_budget(d,'clip_06')
    assert budget['constraints']['clip_01']['used']==1
    assert budget['constraints']['clip_01']['limit']==3
    assert history.remaining_takes(d,'clip_06')==2


def output(d,cid):
    p=d/'work/clips'/cid/'attempt_01/clip.mp4';p.parent.mkdir(parents=True,exist_ok=True);return p


def test_concurrent_children_cannot_overbook_parent(repacked_episode):
    d=repacked_episode;state=history.load(d);state['prior_generation_counts']['clips']['clip_01']=2;history.save(d,state)
    def reserve(cid):
        try:return history.reserve_scope_submission(d,output(d,cid),{'clip_id':cid,'prompt':'new request'})
        except ValueError:return None
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(reserve,['clip_01','clip_06']))
    assert sum(r is not None for r in results)==1
    assert history.source_budget(d,'clip_06')['remaining']==0


def test_pending_unknown_submission_stays_reserved(repacked_episode):
    d=repacked_episode;p=output(d,'clip_06')
    history.reserve_scope_submission(d,p,{'clip_id':'clip_06','prompt':'new'})
    history.finish_scope_submission(d,p,outcome='unknown')
    assert history.source_budget(d,'clip_01')['constraints']['clip_01']['pending']==1
    history.finish_scope_submission(d,p,outcome='not_accepted')
    assert history.source_budget(d,'clip_01')['remaining']==2


def test_same_task_resume_spends_no_second_slot(repacked_episode):
    d=repacked_episode;p=output(d,'clip_06');request={'clip_id':'clip_06','prompt':'new'}
    first=history.reserve_scope_submission(d,p,request)
    atomic_write_json(p.with_suffix('.mp4.task.json'),{'task_id':'old-task'})
    second=history.reserve_scope_submission(d,p,first)
    assert first['_repack_budget']==second['_repack_budget']
    assert history.source_budget(d,'clip_01')['constraints']['clip_01']['pending']==1


def test_completed_take_replaces_reservation_instead_of_double_count(repacked_episode):
    d=repacked_episode;p=output(d,'clip_06')
    history.reserve_scope_submission(d,p,{'clip_id':'clip_06','prompt':'new'})
    s=history.load(d);s['prior_generation_counts']['clips']['clip_06']=1;history.save(d,s)
    history.finish_scope_submission(d,p,outcome='completed')
    assert history.source_budget(d,'clip_01')['constraints']['clip_01']['used']==2
    assert history.source_budget(d,'clip_01')['constraints']['clip_01']['pending']==0


def test_mixed_parent_limits_keep_each_authorization(repacked_episode):
    d=repacked_episode;s=history.load(d);group=s['trials'][0]['changes']['groups'][0]
    group['old'].append('clip_02');group['source_budget']['roots']['clip_02']={'limit':2,'used':2}
    group['source_budget']['children']['clip_06']['roots'].append('clip_02');history.save(d,s)
    assert history.remaining_takes(d,'clip_06')==0
    assert history.remaining_takes(d,'clip_01')==2


def test_repack_does_not_allow_second_extra(repacked_episode):
    d=repacked_episode;s=history.load(d);s['prior_generation_counts']['clips']['clip_01']=4;history.save(d,s)
    atomic_write_json(d/'repair_budget_grants.json',{'clip_01':{'limit':4,'reason':'old approved extra',
        'conditional_extra':{'limit':4,'authorization':'one-extra','reason':'reviewed','activated':True,'prepared':True}}})
    with pytest.raises(ValueError,match='already|used'):
        history.grant_conditional_extra(d,'clip_06',authorization='different-label',reason='retry again')


@pytest.fixture
def long_context(tmp_path,monkeypatch):
    d,base,plan=split_episode.__wrapped__(tmp_path,monkeypatch)
    from novel_manga.application.packing import context,service
    script={'shots':[]}
    for index,seconds in enumerate([5,5,6,6,6,7],1):
        shot=copy.deepcopy(base['shots'][0]);shot.update(index=index,origin_index=index,duration_seconds=seconds,
            visual_prompt='林凡站在窗边',motion_prompt='林凡看向大厅',turns=[])
        script['shots'].append(shot)
    legacy=copy.deepcopy(plan);legacy['limits'].update(max_clip_seconds=30,soft_cut_seconds=30,max_stages=5)
    ctx=context.context_for_plan(d,d.parent/'story_bible.json',legacy)
    shots=service.prepared_shots(copy.deepcopy(script),d)
    legacy['clips']=[service.clip_entry({'kind':'video','shots':shots[:5],'seconds':28,'location':'大厅'},'clip_01',ctx),
                     service.clip_entry({'kind':'video','shots':shots[5:],'seconds':7,'location':'大厅'},'clip_02',ctx)]
    atomic_write_json(d/'chapter_script.json',script);atomic_write_json(d/'clip_plan.json',legacy)
    run=tmp_path/'run';run.mkdir();(run/'source.txt').write_text('林凡走进大厅。')
    work=run/'sandbox';work.mkdir()
    from PIL import Image
    for c in legacy['clips']:
        for ref in c.get('references',[]):
            pic=d.parent/ref['path'];pic.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(32,32),'blue').save(pic)
    legacy=read_json(d/'clip_plan.json')
    return SimpleNamespace(episode_dir=d,novel_dir=d.parent,source_novel_dir=d.parent,asset_snapshot_dir=d.parent,run_dir=run,work_dir=work,clip_seconds_max=15),legacy


def decision():return {'clip_id':'clip_01','action':'repack','clip_ids':['clip_01'],
                       'source_quote':'林凡走进大厅','reason':'原范围超过H3上限'}


def test_unaffected_clip_and_request_bytes_are_identical(long_context):
    ctx,before=long_context;proposal=scoped_repack.prepare(ctx,decision())
    assert proposal.plan['clips'][-1]==before['clips'][-1]
    assert all(c['request_seconds']<=15 for c in proposal.plan['clips'])
    assert proposal.script==read_json(ctx.episode_dir/'chapter_script.json')


def test_parent_28s_video_15s_is_not_auto_reused(long_context):
    ctx,_=long_context;p=output(ctx.episode_dir,'clip_01');p.write_bytes(b'old 15-second take')
    history.save(ctx.episode_dir,{'prior_generation_counts':{'clips':{'clip_01':1}},'trials':[]})
    result=scoped_repack.apply(ctx,decision())
    assert result['ok'] and not result.get('accepted')
    assert history.generation_counts(ctx.episode_dir)['clip_01']==1
    assert p.read_bytes()==b'old 15-second take'


def test_7s_existing_clip_survives_other_ranges_repack(long_context):
    ctx,before=long_context;p=output(ctx.episode_dir,'clip_02');p.write_bytes(b'7-second good take')
    scoped_repack.apply(ctx,decision())
    assert read_json(ctx.episode_dir/'clip_plan.json')['clips'][-1]==before['clips'][-1]
    assert p.read_bytes()==b'7-second good take'


def test_dialogue_stream_preserves_owners_words_modes(long_context):
    from novel_manga.application.packing.blocked import turn_stream
    ctx,_=long_context;before=read_json(ctx.episode_dir/'chapter_script.json')
    proposal=scoped_repack.prepare(ctx,decision())
    assert turn_stream(proposal.script['shots'])==turn_stream(before['shots'])



def test_existing_unused_extra_is_shared_and_activated_for_verified_child(repacked_episode):
    d=repacked_episode;s=history.load(d);s['prior_generation_counts']['clips']['clip_06']=2;history.save(d,s)
    assert history.remaining_takes(d,'clip_06')==0
    extra=history.grant_conditional_extra(d,'clip_06',authorization='one-extra',reason='new verified request')
    assert history.remaining_takes(d,'clip_06',preparation=True)==1
    assert history.generation_limit(d,'clip_06')==3
    history.mark_extra_prepared(d,{'clip_06'})
    assert history.extra_take_ready(d,'clip_06')
    assert not history.extra_take_ready(d,'clip_01')
    assert history.activate_extra_take(d,'clip_06')
    assert history.remaining_takes(d,'clip_06')==1
    s=history.load(d);s['prior_generation_counts']['clips']['clip_06']=3;history.save(d,s)
    assert history.remaining_takes(d,'clip_01')==0
    with pytest.raises(ValueError,match='used'):
        history.grant_conditional_extra(d,'clip_01',authorization='again',reason='another retry')
