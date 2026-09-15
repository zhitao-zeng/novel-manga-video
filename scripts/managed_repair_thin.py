"""Route current failed clips through source, request or generation repair.

The episode remains the exclusive worker unit. Eligibility and effective retry
counts belong to each clip, so a later finding can use the same worker safely.
"""
from __future__ import annotations

import copy
from pathlib import Path
import time

from novel_manga.util import atomic_write_json
from repair_review_thin import read, current_takes
import repair_history as history
from h3_request_checks import request_issues, correction

MAX_GENERATED_TAKES = 3


def generation_limit(directory: Path, cid: str) -> int:
    """Explicit clip-level grants after a reviewed change; history is retained."""
    grant = read(directory/'repair_budget_grants.json',{}).get(cid,{})
    return int(grant.get('limit',MAX_GENERATED_TAKES)) if grant.get('reason') else MAX_GENERATED_TAKES


def source_state(directory: Path, clip: dict) -> dict:
    script = read(directory/'chapter_script.json',{})
    names=set(clip.get('cast',[]))|{r.get('name') for r in clip.get('references',[]) if r.get('role')=='character'}
    bible=read(directory.parent/'story_bible.json',{})
    paths=[directory.parent/name for name in ['entity_index.json','bible_aliases.json']]
    paths += [directory.parent/r['path'] for r in clip.get('references',[]) if r.get('path') and r.get('role')!='voice']
    assets={str(p):[p.stat().st_mtime_ns,p.stat().st_size] if p.is_file() else None for p in paths}
    return {'stages':[s for i,s in enumerate(script.get('shots',[]),1)
                      if s.get('index',i) in clip.get('shot_indexes',[])],
            'segments':read(directory/'segments.json',[]),
            'cast':clip.get('cast',[]),'references':clip.get('references',[]),'crowd_roles':clip.get('crowd_roles',{}),
            'characters':[c for c in bible.get('characters',[]) if c['name'] in names],'assets':assets,
            'speaker_facts':[r for r in read(directory/'source_speaker_contract.json',[]) if r.get('stage') in clip.get('shot_indexes',[])]}


def input_state(directory: Path, clip: dict, verdict: dict, takes: dict) -> dict:
    return {'clip':history.accepted_clip_material(clip),
            'note':read(directory/'review_feedback.json',{}).get(clip['clip_id'],''),
            'source':source_state(directory,clip),'take':takes.get(clip['clip_id']),
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
    for clip in plan.get('clips',[]):
        cid = clip['clip_id'];verdict = review.get('clips',{}).get(cid,{})
        if ((cid not in review.get('feedback',{}) and not request_issues(clip)) or verdict.get('technical') or clip.get('kind')!='video'
                or not takes.get(cid) or verdict.get('video')!=takes[cid]['video'] or verdict.get('take')!=takes[cid]['take']):
            continue
        if counts.get(cid,0)>=generation_limit(directory,cid):
            blocked[cid]='effective clip retry budget used'
        elif (decisions.get(cid,{}).get('status')=='blocked'
              and decisions[cid].get('inputs')==input_state(directory,clip,verdict,takes)):
            blocked[cid]=decisions[cid].get('reason','preparation requires corrected inputs')
        else:
            wanted.append(cid)
    return wanted,blocked


def prepare(directory: Path, targets: list[str] | None = None) -> dict:
    from diagnose_clip_repair import clip_context, diagnose_numbered
    from repair_clips_thin import repair_episode
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
        changed.extend(structural['changed'])
        # A recut clip ID no longer denotes the picture the old judge saw.
        # Render/review the corrected ranges before deciding its next repair.
        wanted = [cid for cid in wanted if cid not in changed]
    for cid in wanted:
        plan = read(directory/'clip_plan.json',{});review = read(directory/'episode_review.json',{})
        clip = next((c for c in plan['clips'] if c['clip_id']==cid),None)
        if clip is None:
            continue  # a corrected cut can retire an old clip ID
        verdict = review.get('clips',{}).get(cid,{})
        precise = verdict.get('verify') or {}
        before = input_state(directory,clip,verdict,current_takes(directory,plan,review))
        previous = decisions.get(cid,{})
        verified_source = previous.get('source_checked')==source_state(directory,clip)
        problem = request_issues(clip)
        attribution = any(precise.get(k) for k in ['action_by_wrong_person','actor_missing','species_or_gender_wrong','lead_face_swapped'])
        try:
            if problem or (attribution and not verified_source):
                action = 'source'
                diagnosis = {'cause':'request_mismatch' if problem else 'source_attribution',
                             'reason':correction(clip) if problem else 'check source attribution before acting on the old verdict'}
            else:
                diagnosis = diagnose_numbered(clip_context(directory.parent,int(directory.name.rsplit('_',1)[1]),cid))
                action = 'retake' if diagnosis.get('cause')=='generation_mismatch' else 'source'
            # A repeated failure gets a new scene expression before another take.
            if action=='retake' and history.repeated_errors(directory,cid):
                action='reframe'
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
                proposal=result['proposal'];updated=proposal['plan']
                entry = next(c for c in updated['clips'] if c['clip_id']==cid)
                note = proposal['notes'].get(cid,'')
                convert(entry,note=note)
                if h3_prompt_outdated(entry,note) or request_issues(entry):
                    raise ValueError('reframed English request still needs correction')
                if (history.accepted_clip_material(entry)==history.accepted_clip_material(clip)
                        and note==read(directory/'review_feedback.json',{}).get(cid,'')):
                    raise ValueError('reframe did not change the failing request')
                entry['repair_take']=int(clip.get('repair_take',0))+1
                history.begin_trial(directory,{cid},'reframe',after_plan=updated,after_notes=proposal['notes'],changes=proposal['changes'])
                atomic_write_json(directory/'chapter_script.json',proposal['script'])
                atomic_write_json(directory/'clip_plan.json',updated)
                atomic_write_json(directory/'review_feedback.json',proposal['notes'])
                changed.append(cid)
            else:
                # A new seed is a real generation attempt without a spoken
                # director tail, nor accidental reuse of the already bad take.
                updated = copy.deepcopy(plan)
                entry = next(c for c in updated['clips'] if c['clip_id']==cid)
                entry['repair_take']=int(entry.get('repair_take',0))+1
                history.begin_trial(directory,{cid},'generation_retry',after_plan=updated,
                                    changes={cid:diagnosis})
                atomic_write_json(directory/'clip_plan.json',updated)
                changed.append(cid)
            after_plan = read(directory/'clip_plan.json',{})
            after_clip = next(c for c in after_plan['clips'] if c['clip_id']==cid)
            decisions[cid]={'at':time.strftime('%F %T'),'action':action,'diagnosis':diagnosis,'status':'prepared',
                            'inputs':before,**({'source_checked':source_state(directory,after_clip)} if action=='source' or verified_source else {})}
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
