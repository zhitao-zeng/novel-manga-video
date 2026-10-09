"""Budget constraints for adopted recuts, without changing physical take counts."""
from pathlib import Path
from novel_manga.util import read_json

FIELD='_repack_budget'


def group_for(directory, cid, record=None):
    record=read_json(Path(directory)/'repair_history/history.json',{}) if record is None else record
    for trial in reversed(record.get('trials',[])):
        for group in (trial.get('changes')or{}).get('groups',[]):
            budget=group.get('source_budget') or {}
            if cid in budget.get('children',{}) or cid in budget.get('roots',{}):return group
    return None


def balances(directory, cid, counts, grants, limits, *, preparation=False):
    group=group_for(directory,cid)
    if not group:return {'roots':[],'constraints':{},'remaining':None}
    budget=group['source_budget'];children=budget['children']
    roots=children.get(cid,{}).get('roots') or [cid]
    constraints={}
    for root in roots:
        original=budget['roots'][root]
        used=int(original['used'])+sum(max(0,counts.get(child,0)-int(info['baseline_count']))
            for child,info in children.items() if root in info['roots'])
        pending=0
        for path in (Path(directory)/'work/clips').glob('*/attempt_*/request.json'):
            request=read_json(path,{});reserved=request.get(FIELD)or{}
            if (root in reserved.get('roots',[]) and reserved.get('status') in {'reserved','unknown'}
                    and counts.get(request.get('clip_id'),0)<=int(reserved.get('before_count',0))):pending+=1
        grant=grants.get(root,{})
        limit=limits[root] if grant.get('reason') and 'limit' in grant else min(int(original['limit']),limits[root])
        extra=grant.get('conditional_extra')or{}
        if preparation and extra.get('authorization') and extra.get('reason'):limit=max(limit,int(extra['limit']))
        constraints[root]={'limit':limit,'used':used,'pending':pending,'remaining':max(0,limit-used-pending)}
    return {'roots':roots,'constraints':constraints,'remaining':min(c['remaining']for c in constraints.values())}


def members(directory,cid,record=None):
    group=group_for(directory,cid,record)
    return set(group['old'])|set(group['new']) if group else {cid}
