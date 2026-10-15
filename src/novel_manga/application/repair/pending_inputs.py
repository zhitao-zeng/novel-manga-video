"""Keep checked inputs as candidates until their entire scope can be rendered."""
from __future__ import annotations
import copy
import time
from pathlib import Path
from novel_manga.util import atomic_write_json, read_json
from novel_manga.application.repair import history
from novel_manga.repair.proposal import RepairProposal


def stage(directory, proposal, method, *, reason):
    path=directory/'work/pending_inputs'/f'{method}-{time.time_ns()}.json'
    atomic_write_json(path,{'status':'needs_video_permission','reason':reason,
                            **proposal.as_result(include_proposal=True)})
    return {'published':False,'staged_candidate':str(path),'needs_video':proposal.changed}


def exhausted_scope(directory, cids, counts):
    exhausted=[cid for cid in cids
               if counts.get(cid,0)>=history.preparation_limit(directory,cid)]
    required={};balances={}
    for cid in cids:
        scope=history.source_budget(directory,cid,preparation=True)
        for root in scope['roots']:
            required[root]=required.get(root,0)+1
            balances[root]=scope['constraints'][root]['remaining']
    exhausted.extend(root for root,needed in required.items() if needed>balances[root])
    return exhausted


def prepare_publication(directory, proposal, method, *, counts):
    from .publication import publish_candidate
    exhausted=exhausted_scope(directory,proposal.changed,counts)
    if exhausted:
        return stage(directory,proposal,method,reason='no remaining video permission for '+', '.join(exhausted))
    publish_candidate(directory,proposal,method,set(proposal.changed))
    history.mark_extra_prepared(directory,proposal.changed)
    for cid in proposal.changed:
        if history.extra_take_ready(directory,cid):history.activate_extra_take(directory,cid)
    return {'published':True}


def recover_unrenderable(directory):
    """Finish the old input-publication transaction without discarding its proposal.

    Older workers published a zero-budget source edit before noticing it could
    never be rendered. Recover only the last untouched, unrendered transaction,
    using its actual sandbox source and history snapshot. Never restore footage,
    clear findings or alter trial/count records.
    """
    from .agent import load_config
    from .publication import write_artifacts
    from novel_manga.application.profiles import plan_fingerprint
    record=history.load(directory)
    if not record.get('trials'):return None
    trial=record['trials'][-1]
    if trial.get('method')!='agent_input' or trial.get('renders'):return None
    if history.owned_generations(directory,record).get(trial['id']):return None
    plan=read_json(directory/'clip_plan.json',{})
    if trial.get('expected_plan')!=plan_fingerprint(plan):return None
    cids=trial['clips'];counts=history.generation_counts(directory)
    if not exhausted_scope(directory,cids,counts):return None
    notes=read_json(directory/'review_feedback.json',{})
    if notes!=trial.get('expected_notes'):return None
    before=trial.get('before')or{}
    if any(not before.get(cid,{}).get('plan') for cid in cids):return None
    if all(next((c for c in plan.get('clips',[]) if c['clip_id']==cid),None)==before[cid]['plan'] for cid in cids):return None
    script=read_json(directory/'chapter_script.json',{})
    restored_script=copy.deepcopy(script);restored_plan=copy.deepcopy(plan);restored_notes=copy.deepcopy(notes)
    source_shots={}
    for cid in cids:
        run=(trial.get('changes',{}).get(cid)or{}).get('run')
        if not run:return None
        primary=read_json(Path(load_config()['runs_root'])/run/'input/primary.json',{})
        for shot in primary.get('shots_and_neighbours',[]):
            index=shot.get('index')
            if index in before[cid]['plan'].get('shot_indexes',[]):
                if index in source_shots and source_shots[index]!=shot:return None
                source_shots[index]=shot
        if set(before[cid]['plan'].get('shot_indexes',[]))-source_shots.keys():return None
        if before[cid].get('note'):restored_notes[cid]=before[cid]['note']
        else:restored_notes.pop(cid,None)
    restored_script['shots']=[copy.deepcopy(source_shots.get(s.get('index',i),s))
                              for i,s in enumerate(script.get('shots',[]),1)]
    restored_plan['clips']=[copy.deepcopy(before[c['clip_id']]['plan']) if c['clip_id'] in cids else c
                            for c in plan['clips']]
    # Retain the exact rejected publication and its source before restoration.
    candidate=RepairProposal({'changed':cids,'recovered_from_trial':trial['id']},
                              script=script,plan=plan,notes=notes,changes=trial.get('changes'))
    result=stage(directory,candidate,'recovered-agent-input',reason='previous worker published input with no video permission')
    write_artifacts(directory,RepairProposal({'changed':cids},script=restored_script,
                     plan=restored_plan,notes=restored_notes))
    result['restored_trial']=trial['id']
    return result
