"""Route current failed clips through source, request or generation repair.

The episode remains the exclusive worker unit. Eligibility and effective retry
counts belong to each clip, so a later finding can use the same worker safely.
"""
from __future__ import annotations

import copy
from pathlib import Path
import time

from novel_manga.repair.policy import source_decision, diagnosed_decision
from novel_manga.repair.proposal import RepairProposal
from novel_manga.repair.execution import retake_proposal
from repair_publication_thin import publish_candidate, publish_retake
from novel_manga.util import atomic_write_json
from novel_manga.util import read_json as read
from review_store_thin import current_takes
import repair_history as history
from novel_manga.story.h3 import request_issues, correction
from repair_inputs_thin import RepairInputs

MAX_GENERATED_TAKES = 3


def generation_limit(directory: Path, cid: str, *, grants: dict | None = None) -> int:
    """Explicit clip-level grants after a reviewed change; history is retained."""
    grants = read(directory/'repair_budget_grants.json',{}) if grants is None else grants
    grant = grants.get(cid,{})
    return int(grant.get('limit',MAX_GENERATED_TAKES)) if grant.get('reason') else MAX_GENERATED_TAKES


def source_state(directory: Path, clip: dict, *, inputs: RepairInputs | None = None) -> dict:
    inputs = inputs if inputs is not None else RepairInputs.load(directory)
    script = inputs.script
    names=set(clip.get('cast',[]))|{r.get('name') for r in clip.get('references',[]) if r.get('role')=='character'}
    bible=inputs.identity.catalog.bible
    paths=[directory.parent/name for name in ['entity_index.json','bible_aliases.json']]
    paths += [directory.parent/r['path'] for r in clip.get('references',[]) if r.get('path') and r.get('role')!='voice']
    assets={str(p):inputs.stat(p) for p in paths}
    identity_context = inputs.identity.context
    source_identity = {k: [{field:value for field,value in row.items() if field!='source_quote'}
                          for row in identity_context.get(k, [])] for k in ['mentions','relations','appearances']}
    from dialogue_binding import POLICY as BINDING_POLICY
    return {'binding_policy': BINDING_POLICY, 'identity_reading': source_identity, 'stages':[s for i,s in enumerate(script.get('shots',[]),1)
                      if s.get('index',i) in clip.get('shot_indexes',[])],
            'segments':inputs.identity.segments,
            'cast':clip.get('cast',[]),'references':clip.get('references',[]),'crowd_roles':clip.get('crowd_roles',{}),
            'characters':[c for c in bible.get('characters',[]) if c['name'] in names],'assets':assets,
            'speaker_facts':[r for r in inputs.speaker_facts if r.get('stage') in clip.get('shot_indexes',[])]}


def input_state(directory: Path, clip: dict, verdict: dict, takes: dict, *, inputs: RepairInputs | None = None) -> dict:
    inputs = inputs if inputs is not None else RepairInputs.load(directory)
    return {'clip':history.accepted_clip_material(clip),
            'note':inputs.notes.get(clip['clip_id'],''),
            'source':source_state(directory,clip,inputs=inputs),'take':takes.get(clip['clip_id']),
            'evidence':(verdict.get('verify') or {}).get('evidence',verdict.get('story_issue',''))}


def generated_counts(record: dict) -> dict[str,int]:
    """Only actual takes produced through the integrated route spend its budget.

    Legacy episode counters are retained as history, not interpreted as attempts
    on every clip. A retry/read of the same media record counts only once.
    """
    by_clip = {}
    for trial in record.get('trials',[]):
        if not trial.get('managed'):
            continue
        for render in trial.get('renders',[]):
            for cid,row in render.get('clips',{}).items():
                for take in row.get('generated_takes',[]):
                    by_clip.setdefault(cid,set()).add((take['video'],tuple(take['take'])))
    return {cid:len(takes) for cid,takes in by_clip.items()}


def candidates(directory: Path, review: dict | None = None) -> tuple[list[str],dict]:
    plan = read(directory/'clip_plan.json',{})
    review = review if review is not None else read(directory/'episode_review.json',{})
    takes = current_takes(directory,plan,review)
    decisions = read(directory/'repair_routing.json',{})
    counts = generated_counts(history.load(directory))
    wanted,blocked = [],{}
    grants = read(directory/'repair_budget_grants.json',{})
    inputs = None
    for clip in plan.get('clips',[]):
        cid = clip['clip_id'];verdict = review.get('clips',{}).get(cid,{})
        if ((cid not in review.get('feedback',{}) and not request_issues(clip)) or verdict.get('technical') or clip.get('kind')!='video'
                or not takes.get(cid) or verdict.get('video')!=takes[cid]['video'] or verdict.get('take')!=takes[cid]['take']):
            continue
        if counts.get(cid,0)>=generation_limit(directory,cid,grants=grants):
            blocked[cid]='effective clip retry budget used'
        else:
            previous = decisions.get(cid, {})
            if previous.get('status') == 'blocked':
                inputs = inputs if inputs is not None else RepairInputs.load(directory)
                if previous.get('inputs') == input_state(directory, clip, verdict, takes, inputs=inputs):
                    blocked[cid] = previous.get('reason', 'preparation requires corrected inputs')
                    continue
            wanted.append(cid)
    return wanted,blocked


def prepare(directory: Path, targets: list[str] | None = None) -> dict:
    from diagnose_clip_repair import clip_context, diagnose_numbered
    from repair_flow_thin import repair_episode
    from source_recheck_thin import prepare_source_recheck
    from build_h3_prompts import convert
    from thin_profile import plan_fingerprint, h3_prompt_outdated
    from repair_blocked_plan import repair_episode as repack_episode

    eligible,blocked = candidates(directory)
    wanted = [cid for cid in eligible if targets is None or cid in targets]
    decisions = read(directory/'repair_routing.json',{})
    initial_count = len(history.load(directory)['trials'])
    changed,accepted = [],[]
    if wanted:
        structural = repack_episode(directory,apply=True)
        changed.extend(dict.fromkeys([*structural['changed'],
                                     *(cid for group in structural.get('groups', []) for cid in group['new'])]))
        # A recut clip ID no longer denotes the picture the old judge saw.
        # Render/review the corrected ranges before deciding its next repair.
        wanted = [cid for cid in wanted if cid not in changed]
    for cid in wanted:
        if cid in changed:
            continue  # a previous repair recut this source-connected range
        plan = read(directory/'clip_plan.json',{});review = read(directory/'episode_review.json',{})
        clip = next((c for c in plan['clips'] if c['clip_id']==cid),None)
        if clip is None:
            continue  # a corrected cut can retire an old clip ID
        verdict = review.get('clips',{}).get(cid,{})
        precise = verdict.get('verify') or {}
        before = input_state(directory,clip,verdict,current_takes(directory,plan,review))
        previous = decisions.get(cid,{})
        verified_source = previous.get('source_checked')==before['source']
        problem = request_issues(clip)
        try:
            decision = source_decision(problem, precise, verified_source, correction(clip) if problem else '')
            if decision is None:
                diagnosis = diagnose_numbered(clip_context(directory.parent,int(directory.name.rsplit('_',1)[1]),cid))
                repeated = diagnosis.get('cause') == 'generation_mismatch' and history.repeated_errors(directory,cid)
                decision = diagnosed_decision(diagnosis, repeated)
            action, diagnosis = decision.action, decision.diagnosis
            print(f'{cid}: route={action}; {diagnosis.get("cause")}',flush=True)
            if action=='source':
                result = prepare_source_recheck(directory,[cid],instructions={cid:correction(clip)})
                changed.extend(result['changed']);accepted.extend(result['accepted'])
            elif action=='reframe':
                issue = str(verdict.get('feedback') or verdict.get('story_issue') or '按原文修正画面')
                result = repair_episode(directory.parent,int(directory.name.rsplit('_',1)[1]),False,
                                        reframe=True,source_issues={cid:issue},return_proposal=True)
                if cid not in result.get('changed',[]):
                    if result.get('proposal'):
                        # Repeated historical errors can route an already
                        # corrected plan here. Judge its footage against the
                        # source instead of demanding arbitrary prompt edits.
                        result = prepare_source_recheck(directory,[cid])
                        changed.extend(result['changed']);accepted.extend(result['accepted'])
                        after_plan = read(directory/'clip_plan.json',{})
                        after_clip = next(c for c in after_plan['clips'] if c['clip_id']==cid)
                        decisions[cid]={'at':time.strftime('%F %T'),'action':'source','diagnosis':diagnosis,
                                        'status':'prepared','inputs':before,'source_checked':source_state(directory,after_clip)}
                        atomic_write_json(directory/'repair_routing.json',decisions)
                        continue
                    raise ValueError('reframe made no effective correction: '+str(result.get('why','')))
                candidate = RepairProposal.from_result(result)
                proposal = candidate.payload
                updated = candidate.plan
                affected = set(result['changed'])
                for entry in updated['clips']:
                    if entry['clip_id'] not in affected:
                        continue
                    note = proposal['notes'].get(entry['clip_id'],'')
                    convert(entry,note=note)
                    if h3_prompt_outdated(entry,note) or request_issues(entry):
                        raise ValueError('reframed English request still needs correction')
                entry = next((c for c in updated['clips'] if c['clip_id']==cid), None)
                if (not proposal.get('structural_repair') and entry is not None
                        and history.accepted_clip_material(entry)==history.accepted_clip_material(clip)
                        and proposal['notes'].get(cid,'')==read(directory/'review_feedback.json',{}).get(cid,'')):
                    raise ValueError('reframe did not change the failing request')
                if entry is not None:
                    entry['repair_take']=int(clip.get('repair_take',0))+1
                publish_candidate(directory, candidate, 'reframe', affected)
                changed.extend(result['changed'])
            else:
                # A new seed is a real generation attempt without a spoken
                # director tail, nor accidental reuse of the already bad take.
                candidate = retake_proposal(plan, cid, diagnosis)
                publish_retake(directory, candidate)
                changed.append(cid)
            after_plan = read(directory/'clip_plan.json',{})
            after_clip = next((c for c in after_plan['clips'] if c['clip_id']==cid), None)
            decisions[cid]={'at':time.strftime('%F %T'),'action':action,'diagnosis':diagnosis,'status':'prepared',
                            'inputs':before,**({'source_checked':source_state(directory,after_clip)} if after_clip is not None and (action=='source' or verified_source) else {})}
        except Exception as error:
            reason = str(error)[:350] if isinstance(error,ValueError) else type(error).__name__
            blocked[cid]=reason
            current_plan=read(directory/'clip_plan.json',{});current_clip=next((c for c in current_plan.get('clips',[]) if c['clip_id']==cid),clip)
            decisions[cid]={'at':time.strftime('%F %T'),'status':'blocked','reason':reason,
                            'inputs':input_state(directory,current_clip,verdict,current_takes(directory,current_plan,review))}
            print(f'{cid}: preparation blocked: {reason}',flush=True)
        atomic_write_json(directory/'repair_routing.json',decisions)
    # Source preparation can create multiple trials. Every prepared clip belongs
    # to this render, not only whichever clip happened to be prepared last.
    record = history.load(directory)
    final_plan = read(directory/'clip_plan.json',{})
    final_notes = read(directory/'review_feedback.json',{})
    from single_card_plan import single_card_plan
    normalized = single_card_plan(final_plan,set(changed)-set(accepted))
    if normalized:
        for clip in final_plan['clips']:
            if clip['clip_id'] in normalized:
                convert(clip,note=str(final_notes.get(clip['clip_id'],'')))
        atomic_write_json(directory/'clip_plan.json',final_plan)
    for trial in record['trials'][initial_count:]:
        trial.update(managed=True,expected_plan=plan_fingerprint(final_plan),expected_notes=copy.deepcopy(final_notes))
    if len(record['trials'])>initial_count:
        history.save(directory,record)
    result={'changed':list(dict.fromkeys(changed)),'accepted':accepted,'blocked':blocked,
            'skip_render':not changed,'generated_counts':generated_counts(record)}
    atomic_write_json(directory/'managed_repair_report.json',result)
    return result
