"""One-shot, per-clip authorization within the existing source-family budget."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json


def family(tmp_path, root, targets, sibling=None, source_used=1):
    d=tmp_path/'book_1';d.mkdir()
    children=list(dict.fromkeys([root,*targets,*([sibling] if sibling else [])]))
    atomic_write_json(d/'clip_plan.json',{'clips':[{'clip_id':c,'kind':'video'} for c in children]})
    group={'old':[root],'new':children,'source_budget':{
        'roots':{root:{'limit':3,'used':source_used}},
        'children':{c:{'roots':[root],'baseline_count':source_used if c==root else 0} for c in children}}}
    counts={root:source_used};counts.update({c:0 for c in targets if c!=root})
    history.save(d,{'prior_generation_counts':{'clips':counts},
                    'trials':[{'id':1,'method':'agent_scoped_repack','changes':{'groups':[group]}}]})
    for cid in targets:
        old=d/'repair_history/unknown-retries'/cid/'attempt_01.json'
        atomic_write_json(old,{'clip_id':cid,'_repack_budget':{
            'roots':[root],'before_count':counts.get(cid,0),'status':'unknown'}})
        atomic_write_json(old.with_suffix('.task.json'),{'submit_uncertain_at':100})
    return d


def authorize(d, root, targets):
    policy={'authorization':'user-authorized-ten','roots':{
        root:{'base_limit':3,'children':{c:{'baseline_count':0} for c in targets}}}}
    atomic_write_json(d/'repair_history/authorized-unknown-retries.json',policy)
    return policy


def output(d,cid,attempt='attempt_01'):
    p=d/'work/clips'/cid/attempt/'clip.mp4';p.parent.mkdir(parents=True,exist_ok=True);return p


def reserve(d,cid,attempt='attempt_01'):
    return history.reserve_scope_submission(d,output(d,cid,attempt),{'clip_id':cid,'prompt':'approved'})


def test_two_approved_children_do_not_free_budget_for_another_child(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20');policy=authorize(d,'clip_05',['clip_18','clip_19'])
    assert history.generation_limit(d,'clip_05')==3
    assert history.remaining_takes(d,'clip_18')==history.remaining_takes(d,'clip_19')==1
    assert history.remaining_takes(d,'clip_20')==0
    first=reserve(d,'clip_18')
    assert first['_repack_budget']['authorized_unknown_retry']==policy['authorization']
    assert history.remaining_takes(d,'clip_18')==0
    assert history.remaining_takes(d,'clip_19')==1
    assert history.remaining_takes(d,'clip_20')==0
    second=reserve(d,'clip_19')
    assert second['_repack_budget']['authorized_unknown_retry']==policy['authorization']
    assert history.remaining_takes(d,'clip_18')==history.remaining_takes(d,'clip_19')==0
    assert history.remaining_takes(d,'clip_20')==0
    with pytest.raises(ValueError,match='budget used'):reserve(d,'clip_20')
    # New unknown stays spent; completion of another replaces only its own reservation.
    history.finish_scope_submission(d,output(d,'clip_19'),outcome='unknown')
    state=history.load(d);state['prior_generation_counts']['clips']['clip_18']=1;history.save(d,state)
    history.finish_scope_submission(d,output(d,'clip_18'),outcome='completed')
    assert history.remaining_takes(d,'clip_18')==history.remaining_takes(d,'clip_19')==0
    assert history.remaining_takes(d,'clip_20')==0
    assert read_json(d/'repair_history/authorized-unknown-retries.json')==policy
    assert history.generation_limit(d,'clip_05')==3


def test_three_approved_children_include_root_without_raising_generic_limit(tmp_path):
    targets=['clip_02','clip_09','clip_10']
    d=family(tmp_path,'clip_02',targets,'clip_99',source_used=0);authorize(d,'clip_02',targets)
    assert history.generation_limit(d,'clip_02')==3
    for cid in targets:
        assert history.remaining_takes(d,cid)==1
        reserve(d,cid)
        assert history.remaining_takes(d,cid)==0
    assert history.generation_limit(d,'clip_02')==3
    assert history.remaining_takes(d,'clip_99')==0
    assert history.source_budget(d,'clip_02')['constraints']['clip_02']['pending']==6
    assert history.generation_counts(d).get('clip_02',0)==0


def test_concurrent_authorized_clips_never_let_other_children_steal_slots(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20');authorize(d,'clip_05',['clip_18','clip_19'])
    def run(cid):
        try:reserve(d,cid);return True
        except ValueError:return False
    with ThreadPoolExecutor(3) as pool:out=list(pool.map(run,['clip_18','clip_19','clip_20']))
    assert out==[True,True,False]
    assert all(history.remaining_takes(d,cid)==0 for cid in ['clip_18','clip_19','clip_20'])


def test_authorization_cannot_be_spent_twice_by_two_attempts(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20');authorize(d,'clip_05',['clip_18','clip_19'])
    def run(attempt):
        try:reserve(d,'clip_18',attempt);return True
        except ValueError:return False
    with ThreadPoolExecutor(2) as pool:out=list(pool.map(run,['attempt_01','attempt_02']))
    assert sum(out)==1
    assert history.remaining_takes(d,'clip_18')==0
    assert history.remaining_takes(d,'clip_19')==1


def test_original_live_unknown_is_not_the_new_authorized_submission(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20');authorize(d,'clip_05',['clip_18','clip_19'])
    archive=d/'repair_history/unknown-retries/clip_18/attempt_01.json'
    target=output(d,'clip_18')
    archive.replace(target.parent/'request.json')
    archive.with_suffix('.task.json').replace(target.with_suffix('.mp4.task.json'))
    assert history.remaining_takes(d,'clip_18')==1
    assert history.remaining_takes(d,'clip_19')==1
    assert history.remaining_takes(d,'clip_20')==0
    assert 'authorized_unknown_retry' not in read_json(target.parent/'request.json')['_repack_budget']


def test_reference_failure_before_submit_does_not_consume_authorization(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20');authorize(d,'clip_05',['clip_18','clip_19'])
    reserve(d,'clip_18')
    assert history.remaining_takes(d,'clip_18')==0
    history.finish_scope_submission(d,output(d,'clip_18'),outcome='not_accepted')
    assert history.remaining_takes(d,'clip_18')==1
    assert history.remaining_takes(d,'clip_19')==1
    assert history.remaining_takes(d,'clip_20')==0


def test_stale_authorized_baseline_does_not_expand_source_limit(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20');policy=authorize(d,'clip_05',['clip_18','clip_19'])
    policy['roots']['clip_05']['base_limit']=4
    atomic_write_json(d/'repair_history/authorized-unknown-retries.json',policy)
    assert history.remaining_takes(d,'clip_18')==0
    assert history.remaining_takes(d,'clip_19')==0
    assert history.remaining_takes(d,'clip_20')==0


def test_specific_extra_can_fit_an_exhausted_child_without_changing_grants(tmp_path):
    d=family(tmp_path,'clip_05',['clip_18','clip_19'],'clip_20',source_used=0)
    state=history.load(d);state['prior_generation_counts']['clips']['clip_18']=2;history.save(d,state)
    grants={'clip_05':{'reason':'previous source approval','limit':4}}
    atomic_write_json(d/'repair_budget_grants.json',grants)
    policy=authorize(d,'clip_05',['clip_18','clip_19'])
    policy['roots']['clip_05']['base_limit']=4
    policy['roots']['clip_05']['children']['clip_18']['baseline_count']=2
    atomic_write_json(d/'repair_history/authorized-unknown-retries.json',policy)
    assert history.generation_limit(d,'clip_18')==3
    assert history.remaining_takes(d,'clip_18')==1
    reserve(d,'clip_18')
    assert history.remaining_takes(d,'clip_18')==0
    assert history.generation_limit(d,'clip_18')==3
    assert read_json(d/'repair_budget_grants.json')==grants


