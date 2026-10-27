"""Source limits survive recuts; pending submissions share the actual balance."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import copy
import pytest
from novel_manga.util import atomic_write_json, read_json
from novel_manga.application.repair import history
from novel_manga.application.agents.episode import scoped_repack
from support.split_episode import split_episode




def test_two_children_share_parent_balance(repacked_episode):
    d=repacked_episode
    assert history.generation_counts(d)['clip_01']==1
    assert history.generation_scope(d,'clip_06')=={'clip_01','clip_06'}
    budget=history.source_budget(d,'clip_06')
    assert budget['constraints']['clip_01']['used']==1
    assert budget['constraints']['clip_01']['limit']==3
    assert history.remaining_takes(d,'clip_06')==2




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


def test_authorized_retry_keeps_older_unknown_in_shared_source_budget(repacked_episode):
    d=repacked_episode;video=output(d,'clip_06');request={'clip_id':'clip_06','prompt':'same request'}
    original=history.reserve_scope_submission(d,video,request)
    history.finish_scope_submission(d,video,outcome='unknown')
    original=read_json(video.parent/'request.json')
    old_task={'submit_uncertain_at':100,'model':'MiniMax-H3'}
    task=video.with_suffix('.mp4.task.json');atomic_write_json(task,old_task)
    # The request and task are archived separately, retaining their original fields.
    archive=d/'repair_history/unknown-retries/clip_06/attempt_01.json'
    atomic_write_json(archive,original)
    atomic_write_json(archive.with_suffix('.task.json'),old_task)
    # Archiving precedes removing the active reservation: the brief overlap
    # conservatively reserves both until the release operation completes.
    assert history.source_budget(d,'clip_01')['constraints']['clip_01']['pending']==2
    atomic_write_json(video.parent/'request.json',request)
    task.unlink()
    assert history.source_budget(d,'clip_01')['constraints']['clip_01']['pending']==1
    current=history.reserve_scope_submission(d,video,request)
    assert current['_repack_budget']['status']=='reserved'
    assert history.source_budget(d,'clip_01')['constraints']['clip_01']=={
        'limit':3,'used':1,'pending':2,'remaining':0}
    # A sibling shares this source; it cannot use either occupied slot.
    with pytest.raises(ValueError,match='budget used'):
        history.reserve_scope_submission(d,output(d,'clip_01'),{'clip_id':'clip_01','prompt':'sibling'})

    # A new unconfirmed retry is distinct and keeps its own reservation too.
    history.finish_scope_submission(d,video,outcome='unknown')
    atomic_write_json(task,{'submit_uncertain_at':200,'model':'MiniMax-H3'})
    assert history.source_budget(d,'clip_01')['constraints']['clip_01']['pending']==2

    # Completing the retry replaces only its new reservation, not the old unknown.
    state=history.load(d);state['prior_generation_counts']['clips']['clip_06']=1;history.save(d,state)
    history.finish_scope_submission(d,video,outcome='completed')
    assert history.source_budget(d,'clip_01')['constraints']['clip_01']=={
        'limit':3,'used':2,'pending':1,'remaining':0}
    with pytest.raises(ValueError,match='budget used'):
        history.reserve_scope_submission(d,output(d,'clip_01'),{'clip_id':'clip_01','prompt':'sibling'})


def test_archived_source_unknown_only_counts_its_own_roots(repacked_episode):
    d=repacked_episode
    archive=d/'repair_history/unknown-retries/clip_06/attempt_01.json'
    atomic_write_json(archive,{'clip_id':'clip_06','_repack_budget':{
        'roots':['different_root'],'before_count':0,'status':'unknown'}})
    atomic_write_json(archive.with_suffix('.task.json'),{'submit_uncertain_at':100})
    assert history.source_budget(d,'clip_01')['constraints']['clip_01']['pending']==0


def test_archived_source_reservation_keeps_unknown_after_interrupted_submit(repacked_episode):
    d=repacked_episode
    archive=d/'repair_history/unknown-retries/clip_06/attempt_01.json'
    # A process can stop after the video task became uncertain but before
    # finish_scope_submission changes this request from reserved to unknown.
    atomic_write_json(archive,{'clip_id':'clip_06','_repack_budget':{
        'roots':['clip_01'],'before_count':0,'status':'reserved'}})
    atomic_write_json(archive.with_suffix('.task.json'),{'submit_uncertain_at':100})
    assert history.source_budget(d,'clip_06')['constraints']['clip_01']=={
        'limit':3,'used':1,'pending':1,'remaining':1}
    state=history.load(d);state['prior_generation_counts']['clips']['clip_06']=1;history.save(d,state)
    assert history.source_budget(d,'clip_06')['constraints']['clip_01']=={
        'limit':3,'used':2,'pending':1,'remaining':0}


def test_ordinary_unknown_before_repack_consumes_new_shared_root_budget(repacked_episode):
    d=repacked_episode;state=history.load(d)
    original_changes=state['trials'][0].pop('changes');history.save(d,state)
    archive=d/'repair_history/unknown-retries/clip_01/attempt_01.json'
    atomic_write_json(archive,{'clip_id':'clip_01','prompt':'old ordinary request'})
    atomic_write_json(archive.with_suffix('.task.json'),{'submit_uncertain_at':100})
    assert history.remaining_takes(d,'clip_01')==1

    # A later recut makes the old ordinary clip the source of both children.
    state=history.load(d);state['trials'][0]['changes']=original_changes;history.save(d,state)
    assert history.source_budget(d,'clip_06')['constraints']['clip_01']=={
        'limit':3,'used':1,'pending':1,'remaining':1}
    history.reserve_scope_submission(d,output(d,'clip_06'),{'clip_id':'clip_06','prompt':'new child'})
    assert history.remaining_takes(d,'clip_01')==0
    with pytest.raises(ValueError,match='budget used'):
        history.reserve_scope_submission(d,output(d,'clip_01'),{'clip_id':'clip_01','prompt':'sibling'})


def test_archived_unknown_consumes_ordinary_clip_budget_after_new_success(tmp_path):
    d=tmp_path/'book_1';d.mkdir()
    history.save(d,{'trials':[], 'prior_generation_counts':{'clips':{'clip_06':0}}})
    archive=d/'repair_history/unknown-retries/clip_06/attempt_01.json'
    atomic_write_json(archive,{'clip_id':'clip_06','prompt':'old request'})
    atomic_write_json(archive.with_suffix('.task.json'),{'submit_uncertain_at':100})
    assert history.generation_limit(d,'clip_06')==3
    assert history.remaining_takes(d,'clip_06')==2
    state=history.load(d);state['prior_generation_counts']['clips']['clip_06']=1;history.save(d,state)
    assert history.generation_counts(d)['clip_06']==1
    assert history.remaining_takes(d,'clip_06')==1


def test_old_unknown_cannot_reactivate_an_already_exhausted_extra_take(tmp_path):
    d=tmp_path/'book_1';d.mkdir()
    history.save(d,{'trials':[], 'prior_generation_counts':{'clips':{'clip_06':2}}})
    grants={'clip_06':{'reason':'previous approval','limit':3,
        'conditional_extra':{'authorization':'existing','reason':'prior approval',
                             'prepared':True,'activated':True,'limit':3}}}
    atomic_write_json(d/'repair_budget_grants.json',grants)
    archive=d/'repair_history/unknown-retries/clip_06/attempt_01.json'
    atomic_write_json(archive,{'clip_id':'clip_06','prompt':'old request'})
    atomic_write_json(archive.with_suffix('.task.json'),{'submit_uncertain_at':100})
    assert history.remaining_takes(d,'clip_06')==0
    assert not history.extra_take_ready(d,'clip_06')
    assert not history.activate_extra_take(d,'clip_06')
    assert read_json(d/'repair_budget_grants.json')==grants


def test_archived_unknown_keeps_clip_and_shared_root_limits(repacked_episode):
    d=repacked_episode
    archive=d/'repair_history/unknown-retries/clip_06/attempt_01.json'
    atomic_write_json(archive,{'clip_id':'clip_06','_repack_budget':{
        'roots':['clip_01'],'before_count':0,'status':'unknown'}})
    atomic_write_json(archive.with_suffix('.task.json'),{'submit_uncertain_at':100})
    state=history.load(d);state['prior_generation_counts']['clips']['clip_06']=1;history.save(d,state)
    assert history.generation_counts(d)['clip_06']==1
    assert history.source_budget(d,'clip_06')['constraints']['clip_01']=={
        'limit':3,'used':2,'pending':1,'remaining':0}
    assert history.remaining_takes(d,'clip_06')==0
    assert history.remaining_takes(d,'clip_01')==0


@pytest.mark.parametrize('known_task',[False,True])
def test_reference_failure_releases_only_a_new_unsubmitted_source_slot(repacked_episode,tmp_path,monkeypatch,known_task):
    from support.generation import runner
    from novel_manga.media import generation,cache
    from novel_manga.providers.phanrouter_references import H3ReferenceUnavailable
    d=repacked_episode;r=runner(tmp_path);ctx=r.context
    ctx.work=d/'work';ctx.episode_dir=d;ctx.novel_dir=d.parent
    ctx.settings=SimpleNamespace(local_h3_base_url=None,video_model='sd2.5')
    ctx.clip_plan=read_json(d/'clip_plan.json');ctx.state.remaining={'clip_06':2}
    request={'clip_id':'clip_06','attempt':1,'duration':4,'prompt':'approved input',
             'references':[],'reference_sha256':[],'workflow':'test'}
    p=output(d,'clip_06')
    if known_task:
        history.reserve_scope_submission(d,p,request)
        atomic_write_json(p.with_suffix('.mp4.task.json'),{'task_id':'already-accepted'})
    monkeypatch.setattr(generation,'build_request',lambda *a:(request,(),[],''))
    monkeypatch.setattr(cache,'request_matches',lambda *a:True)
    monkeypatch.setattr(generation,'submit',lambda *a:(_ for _ in ()).throw(
        H3ReferenceUnavailable('image hosting failed before video submission')))
    clip={'clip_id':'clip_06','request_seconds':4,'references':[]}
    with pytest.raises(H3ReferenceUnavailable):r.generate_clip(clip,1)
    budget=history.source_budget(d,'clip_06')['constraints']['clip_01']
    assert budget['used']==1 and budget['pending']==int(known_task)
    assert budget['remaining']==2-int(known_task)
    if known_task:assert read_json(p.with_suffix('.mp4.task.json'))['task_id']=='already-accepted'


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


def test_previously_approved_source_extra_keeps_archived_child_unknown(repacked_episode):
    d=repacked_episode
    state=history.load(d);state['prior_generation_counts']['clips']['clip_06']=2;history.save(d,state)
    archive=d/'repair_history/unknown-retries/clip_06/attempt_01.json'
    atomic_write_json(archive,{'clip_id':'clip_06','_repack_budget':{
        'roots':['clip_01'],'before_count':2,'status':'unknown'}})
    atomic_write_json(archive.with_suffix('.task.json'),{'submit_uncertain_at':100})
    # This source-family extra was already authorized and its input prepared.
    grants={'clip_01':{'conditional_extra':{'authorization':'existing','reason':'reviewed',
        'limit':5,'prepared':True,'prepared_clips':['clip_06'],'activated':False}}}
    atomic_write_json(d/'repair_budget_grants.json',grants)
    assert history.source_budget(d,'clip_06')['remaining']==0
    assert history.source_budget(d,'clip_06',preparation=True)['remaining']==1
    assert history.remaining_takes(d,'clip_06',preparation=True)==1
    assert history.extra_take_ready(d,'clip_06')
    assert history.activate_extra_take(d,'clip_06')
    assert history.generation_limit(d,'clip_06')==4
    assert history.generation_limit(d,'clip_01')==5
    assert history.remaining_takes(d,'clip_06')==1
    history.reserve_scope_submission(d,output(d,'clip_06'),{'clip_id':'clip_06','prompt':'approved'})
    assert history.source_budget(d,'clip_06')['constraints']['clip_01']=={
        'limit':5,'used':3,'pending':2,'remaining':0}
    assert not history.extra_take_ready(d,'clip_06')
    assert not history.activate_extra_take(d,'clip_06')

from support.repack_budget import decision, long_context, output, repacked_episode
