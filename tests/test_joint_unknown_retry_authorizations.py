"""Joint input work can use each approved source-family retry once."""
from novel_manga.application.repair import history
from novel_manga.application.repair.pending_inputs import exhausted_scope
from novel_manga.util import atomic_write_json,read_json
from support.unknown_retry_authorizations import authorize, family, output, reserve


def test_joint_input_preparation_uses_each_approved_child_once(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20');authorize(d,'clip_05',['clip_18','clip_19'])
    counts=history.generation_counts(d)
    assert exhausted_scope(d,['clip_18','clip_19'],counts)==[]
    assert 'clip_05' in exhausted_scope(d,['clip_18','clip_20'],counts)
    reserve(d,'clip_18')
    history.finish_scope_submission(d,output(d,'clip_18'),outcome='unknown')
    assert 'clip_05' in exhausted_scope(d,['clip_18','clip_19'],counts)
    state=history.load(d);state['prior_generation_counts']['clips']['clip_18']=1;history.save(d,state)
    history.finish_scope_submission(d,output(d,'clip_18'),outcome='completed')
    assert 'clip_05' in exhausted_scope(d,['clip_18','clip_19'],history.generation_counts(d))


def test_joint_input_preparation_counts_shared_original_balance_once(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20',source_used=0)
    counts=history.generation_counts(d)
    # Without explicit authorization, the one shared source slot cannot fund two children.
    assert 'clip_05' in exhausted_scope(d,['clip_18','clip_19'],counts)
    authorize(d,'clip_05',['clip_18','clip_19'])
    # One shared original slot plus the two separate approvals funds three clips.
    assert exhausted_scope(d,['clip_18','clip_19','clip_20'],counts)==[]
    assert 'clip_05' in exhausted_scope(d,['clip_18','clip_19','clip_20','clip_05'],counts)


def test_clip_spanning_two_roots_requires_authorization_from_both(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20');policy=authorize(d,'clip_05',['clip_18','clip_19'])
    state=history.load(d);budget=state['trials'][0]['changes']['groups'][0]['source_budget']
    budget['roots']['clip_06']={'limit':3,'used':0}
    budget['children']['clip_18']['roots'].append('clip_06');history.save(d,state)
    old=d/'repair_history/unknown-retries/clip_18/attempt_01.json'
    request=read_json(old);request['_repack_budget']['roots'].append('clip_06');atomic_write_json(old,request)
    # The second root has ordinary room, but only the first root authorized this clip.
    assert history.remaining_takes(d,'clip_18')==0
    policy['roots']['clip_06']={'base_limit':3,'children':{'clip_18':{'baseline_count':0}}}
    atomic_write_json(d/'repair_history/authorized-unknown-retries.json',policy)
    assert history.remaining_takes(d,'clip_18')==1
    reserve(d,'clip_18')
    assert history.remaining_takes(d,'clip_18')==0


def test_joint_input_does_not_publish_for_exhausted_clip_with_shared_room(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20',source_used=0)
    state=history.load(d);state['prior_generation_counts']['clips']['clip_18']=2;history.save(d,state)
    atomic_write_json(d/'repair_budget_grants.json',{'clip_05':{'reason':'prior source approval','limit':5}})
    counts=history.generation_counts(d)
    assert history.source_budget(d,'clip_18',preparation=True)['remaining']==1
    assert history.remaining_takes(d,'clip_18',preparation=True)==0
    assert 'clip_18' in exhausted_scope(d,['clip_18'],counts)


def test_joint_input_can_use_approved_retry_after_clip_reached_original_limit(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20',source_used=0)
    state=history.load(d);state['prior_generation_counts']['clips']['clip_18']=3;history.save(d,state)
    archive=d/'repair_history/unknown-retries/clip_18/attempt_01.json'
    target=output(d,'clip_18');archive.replace(target.parent/'request.json')
    archive.with_suffix('.task.json').replace(target.with_suffix('.mp4.task.json'))
    atomic_write_json(d/'repair_budget_grants.json',{'clip_05':{'reason':'prior source approval','limit':5}})
    policy=authorize(d,'clip_05',['clip_18','clip_19']);policy['roots']['clip_05']['base_limit']=5
    policy['roots']['clip_05']['children']['clip_18']['baseline_count']=3
    atomic_write_json(d/'repair_history/authorized-unknown-retries.json',policy)
    counts=history.generation_counts(d)
    assert counts['clip_18']==history.preparation_limit(d,'clip_18')==3
    assert history.remaining_takes(d,'clip_18',preparation=True)==1
    assert exhausted_scope(d,['clip_18'],counts)==[]


def test_supplied_counts_include_other_child_in_shared_source_budget(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20',source_used=0)
    assert history.generation_counts(d)['clip_18']==0
    assert history.source_budget(d,'clip_19',preparation=True)['remaining']==1
    # The caller has newer confirmed usage for a sibling not in this proposal.
    assert 'clip_05' in exhausted_scope(d,['clip_19'],{'clip_18':1,'clip_19':0})


def test_stale_supplied_counts_do_not_reopen_a_consumed_retry(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20');authorize(d,'clip_05',['clip_18','clip_19'])
    state=history.load(d);state['prior_generation_counts']['clips']['clip_18']=1;history.save(d,state)
    assert history.remaining_takes(d,'clip_18',preparation=True)==0
    assert 'clip_18' in exhausted_scope(d,['clip_18'],{'clip_18':0,'clip_19':0})


def test_newer_supplied_counts_consume_the_targeted_retry_once(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20');authorize(d,'clip_05',['clip_18','clip_19'])
    assert history.remaining_takes(d,'clip_18',preparation=True)==1
    assert 'clip_18' in exhausted_scope(d,['clip_18'],{'clip_18':1,'clip_19':0})
    assert exhausted_scope(d,['clip_19'],{'clip_18':1,'clip_19':0})==[]
