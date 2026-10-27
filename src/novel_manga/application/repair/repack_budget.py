"""Budget constraints for adopted recuts, without changing physical take counts."""
from pathlib import Path
from novel_manga.util import read_json

FIELD='_repack_budget'
RETRY_AUTHORIZATION='authorized_unknown_retry'
RETRY_LEDGER='authorized-unknown-retries.json'


def archived_unknown_count(directory, cid):
    """Each archived request is a prior unresolved attempt for this clip."""
    folder=Path(directory)/'repair_history/unknown-retries'/cid
    return sum(not path.name.endswith('.task.json') for path in folder.glob('*.json'))


def retry_ledger(directory):
    return read_json(Path(directory)/'repair_history'/RETRY_LEDGER,{})


def retry_authorization(directory, cid, roots, *, ledger=None):
    ledger=retry_ledger(directory) if ledger is None else ledger
    authorization=ledger.get('authorization')
    policies=ledger.get('roots') or {}
    if not authorization or not roots:
        return None
    if not all(cid in (policies.get(root,{}).get('children') or {}) for root in roots):
        return None
    return authorization


def retry_consumed(directory, cid, counts, authorization, baseline_count):
    """A completed or unresolved new submission spends this clip's one retry."""
    if counts.get(cid,0)>int(baseline_count):return True
    for path in (Path(directory)/'work/clips'/cid).glob('attempt_*/request.json'):
        reserved=read_json(path,{}).get(FIELD)or{}
        if (reserved.get(RETRY_AUTHORIZATION)==authorization
                and reserved.get('status') in {'reserved','unknown'}
                and counts.get(cid,0)<=int(reserved.get('before_count',0))):return True
    return False


def retry_available(directory, cid, counts, roots):
    ledger=retry_ledger(directory)
    authorization=retry_authorization(directory,cid,roots,ledger=ledger)
    if not authorization:return False
    for root in roots:
        child=ledger['roots'][root]['children'][cid]
        if retry_consumed(directory,cid,counts,authorization,child['baseline_count']):return False
    return True


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
    constraints={};ledger=retry_ledger(directory);authorization=ledger.get('authorization')
    for root in roots:
        original=budget['roots'][root]
        used=int(original['used'])+sum(max(0,counts.get(child,0)-int(info['baseline_count']))
            for child,info in children.items() if root in info['roots'])
        pending=0
        for path in (Path(directory)/'work/clips').glob('*/attempt_*/request.json'):
            request=read_json(path,{});reserved=request.get(FIELD)or{}
            if (root in reserved.get('roots',[]) and reserved.get('status') in {'reserved','unknown'}
                    and counts.get(request.get('clip_id'),0)<=int(reserved.get('before_count',0))):pending+=1
        # An explicitly retried unknown remains a separate possible paid task.
        # Its archived request survives completion of the newer generation.
        for path in (Path(directory)/'repair_history/unknown-retries').glob('*/*.json'):
            if path.name.endswith('.task.json'):continue
            reserved=read_json(path,{}).get(FIELD)or{}
            if reserved:
                if root in reserved.get('roots',[]) and reserved.get('status') in {'reserved','unknown'}:pending+=1
            elif path.parent.name==root:
                # An ordinary unknown can predate this root's later recut.
                pending+=1
        grant=grants.get(root,{})
        limit=limits[root] if grant.get('reason') and 'limit' in grant else min(int(original['limit']),limits[root])
        extra=grant.get('conditional_extra')or{}
        if preparation and extra.get('authorization') and extra.get('reason'):limit=max(limit,int(extra['limit']))
        effective_limit=limit
        policy=(ledger.get('roots') or {}).get(root) or {}
        children_policy=policy.get('children') or {}
        if authorization and policy.get('base_limit')==limit:
            consumed={child:retry_consumed(directory,child,counts,authorization,entry['baseline_count'])
                      for child,entry in children_policy.items()}
            available=retry_available(directory,cid,counts,roots)
            effective_limit+=sum(consumed.values())+int(cid in consumed and not consumed[cid] and available)
        constraints[root]={'limit':limit,'used':used,'pending':pending,'remaining':max(0,effective_limit-used-pending)}
    return {'roots':roots,'constraints':constraints,'remaining':min(c['remaining']for c in constraints.values())}


def members(directory,cid,record=None):
    group=group_for(directory,cid,record)
    return set(group['old'])|set(group['new']) if group else {cid}
