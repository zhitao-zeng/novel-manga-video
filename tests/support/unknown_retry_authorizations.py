"""Test fixtures for scoped H3 unknown retry authorizations."""
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json


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


