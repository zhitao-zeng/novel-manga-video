"""Route current failed clips through source, request or generation repair.

The episode remains the exclusive worker unit. Eligibility and effective retry
counts belong to each clip, so a later finding can use the same worker safely.
"""
from __future__ import annotations
import novel_manga.episodes as ep_names

import copy
from pathlib import Path
import time

from novel_manga.repair.policy import source_decision, diagnosed_decision, whole_take_decision
from novel_manga.repair.proposal import RepairProposal
from novel_manga.repair.execution import retake_proposal
from novel_manga.application.repair.publication import publish_candidate, publish_retake
from novel_manga.util import atomic_write_json
from novel_manga.util import read_json as read
from novel_manga.application.review.store import current_takes
import novel_manga.application.repair.history as history
from novel_manga.story.h3 import request_issues, identity_issues, correction
from novel_manga.application.repair.inputs import RepairInputs

MAX_GENERATED_TAKES = 3
REFRAME_POLICY = 'reframe-v3-reference-state-and-candidate-feedback'


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
    from novel_manga.application.identity.dialogue import POLICY as BINDING_POLICY
    return {'binding_policy': BINDING_POLICY, 'identity_reading': source_identity, 'stages':[s for i,s in enumerate(script.get('shots',[]),1)
                      if s.get('index',i) in clip.get('shot_indexes',[])],
            'segments':inputs.identity.segments, 'segment_ids': clip.get('segment_ids') or [],
            'cast':clip.get('cast',[]),'references':clip.get('references',[]),'crowd_roles':clip.get('crowd_roles',{}),
            'characters':[c for c in bible.get('characters',[]) if c['name'] in names],'assets':assets,
            'speaker_facts':[r for r in inputs.speaker_facts if r.get('stage') in clip.get('shot_indexes',[])]}


def input_state(directory: Path, clip: dict, verdict: dict, takes: dict, *, inputs: RepairInputs | None = None) -> dict:
    inputs = inputs if inputs is not None else RepairInputs.load(directory)
    from novel_manga.application.preparation.request_check import POLICY as INPUT_POLICY
    return {'reframe_policy': REFRAME_POLICY, 'input_check_policy': INPUT_POLICY, 'clip':history.accepted_clip_material(clip),
            'note':inputs.notes.get(clip['clip_id'],''),
            'source':source_state(directory,clip,inputs=inputs),'take':takes.get(clip['clip_id']),
            'error_kinds': (verdict.get('verify') or {}).get('error_kinds', []),
            'request_conflict': (verdict.get('verify') or {}).get('request_conflict', False),
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
    prior = (record.get('prior_generation_counts') or {}).get('clips') or {}
    return {cid:int(prior.get(cid,0))+len(by_clip.get(cid,set())) for cid in set(prior)|set(by_clip)}


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
        if ((cid not in review.get('feedback',{}) and not identity_issues(clip)) or verdict.get('technical') or clip.get('kind')!='video'
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


def reframe_candidate(directory: Path, cid: str, issue: str):
    """One bounded rewrite/check path shared by video repair and first-take input repair."""
    from novel_manga.application.repair.flow import repair_episode
    from novel_manga.application.rendering.h3 import convert
    from novel_manga.application.profiles import h3_prompt_outdated
    from novel_manga.application.preparation.request_check import request_consistency
    request_checks = []
    for revision in range(2):
        result = repair_episode(directory.parent,ep_names.chapter_of(directory.name),False,
                                reframe=True,source_issues={cid:issue},return_proposal=True,episode_dir=directory)
        if cid not in result.get('changed', []):
            if revision == 0 and str(result.get('why', '')).startswith('rebuild failed:'):
                issue += ('\n上次候选未通过场景解析/编译：' + result['why']
                          + '。只修原有输入冲突，保留原稿其他人物姿态、机位、动作、对白和时长。')
                continue
            break
        trial_plan = result['proposal']['plan']
        problems = []
        for checked_entry in trial_plan['clips']:
            if checked_entry['clip_id'] not in result['changed']:
                continue
            note = result['proposal']['notes'].get(checked_entry['clip_id'], '')
            convert(checked_entry, note=note)
            if h3_prompt_outdated(checked_entry, note) or request_issues(checked_entry):
                problems.append('英文请求格式或绑定未通过')
                continue
            checked = request_consistency(checked_entry, directory.parent)
            request_checks.append({'clip_id': checked_entry['clip_id'], 'revision': revision+1, **checked})
            if not checked.get('consistent') or checked.get('problems'):
                problems.extend(checked.get('problems') or ['修复要求未落实'])
        affected = {i for c in trial_plan['clips'] if c['clip_id'] in result['changed'] for i in c.get('shot_indexes', [])}
        atomic_write_json(directory / 'work' / 'request_checks' / f'{cid}.repair-{revision+1}.json', {
            'stage': 'candidate_before_publication', 'revision': revision+1,
            'shots': [s for i,s in enumerate(result['proposal']['script'].get('shots', []), 1)
                      if s.get('index', i) in affected],
            'clips': [c for c in trial_plan['clips'] if c['clip_id'] in result['changed']],
            'checks': request_checks,
            'visor_states': result['proposal'].get('visor_states'),
        })
        if not problems:
            break
        if revision == 1:
            raise ValueError('修后请求仍矛盾：'+'；'.join(problems))
        issue += '\n上一候选在最终请求检查中失败，重新修改源分镜以消除：'+'；'.join(problems)
    return result, request_checks


def prepare_request_conflicts(directory: Path, report: dict) -> dict:
    """A rejected first request has no video to review, but can use the existing rewrite path."""
    from novel_manga.application.preparation.request_check import current_request
    changed, blocked, checks = [], {}, []
    counts = generated_counts(history.load(directory))
    decisions = read(directory / 'repair_routing.json', {})
    for cid, reasons in report.get('blocked_clips', {}).items():
        if cid in changed or not any(r.startswith('request:') for r in reasons):
            continue
        if counts.get(cid, 0) >= generation_limit(directory, cid):
            blocked[cid] = 'effective clip retry budget used'
            continue
        clip = next((c for c in read(directory / 'clip_plan.json', {}).get('clips', []) if c['clip_id'] == cid), None)
        if clip is None or not clip.get('prompt_h3'):
            continue
        before = input_state(directory, clip, {}, {})
        previous = decisions.get(cid, {})
        if previous.get('stage') == 'input_repair' and previous.get('status') == 'blocked' and previous.get('inputs') == before:
            blocked[cid] = previous['reason']
            continue
        try:
            checked = current_request(directory, clip)
            if checked.get('consistent') is True and not checked.get('problems'):
                continue
            from novel_manga.application.repair.reference_state import propose as reference_state_proposal
            candidate = reference_state_proposal(directory, clip, checked)
            if candidate is not None:
                publish_candidate(directory, candidate, 'reference_state_recheck', set(candidate.changed))
                record = history.load(directory); record['trials'][-1]['managed'] = True; history.save(directory, record)
                changed.extend(candidate.changed)
                decisions[cid] = {'stage': 'input_repair', 'status': 'prepared', 'inputs': before}
                atomic_write_json(directory / 'repair_routing.json', decisions)
                continue
            issue = ('实际请求与参考图冲突，生成尚未开始。保留原文、台词、时长和身份，只修冲突。'
                     '\n参考图观察：' + '；'.join(checked.get('observations', []))
                     + '\n冲突：' + '；'.join(checked.get('problems') or reasons))
            result, evidence = reframe_candidate(directory, cid, issue)
            checks.extend(evidence)
            if cid not in result.get('changed', []):
                raise ValueError('no effective input correction: ' + str(result.get('why', '')))
            candidate = RepairProposal.from_result(result)
            publish_candidate(directory, candidate, 'request_consistency', set(candidate.changed))
            record = history.load(directory); record['trials'][-1]['managed'] = True; history.save(directory, record)
            changed.extend(candidate.changed)
            decisions[cid] = {'stage': 'input_repair', 'status': 'prepared', 'inputs': before}
        except Exception as error:
            blocked[cid] = str(error)[:350]
            decisions[cid] = {'stage': 'input_repair', 'status': 'blocked', 'inputs': before, 'reason': blocked[cid]}
        atomic_write_json(directory / 'repair_routing.json', decisions)
    return {'changed': sorted(set(changed)), 'blocked': blocked, 'skip_render': not changed,
            'request_checks': checks, 'stage': 'input_repair'}


def prepare(directory: Path, targets: list[str] | None = None) -> dict:
    from novel_manga.application.repair.diagnosis import clip_context, diagnose_numbered
    from novel_manga.application.repair.flow import repair_episode
    from novel_manga.application.repair.source_recheck import prepare_source_recheck
    from novel_manga.application.rendering.h3 import convert
    from novel_manga.application.profiles import plan_fingerprint, h3_prompt_outdated
    from novel_manga.application.packing.blocked import repair_episode as repack_episode

    history.adopt_reviewed_run(directory)
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
        problem = identity_issues(clip)
        try:
            decision = source_decision(problem, precise, verified_source, correction(clip) if problem else '')
            if decision is None and clip.get('prompt_h3') and any(r.get('role') != 'voice' for r in clip.get('references', [])):
                from novel_manga.application.preparation.request_check import current_request
                checked_input = current_request(directory, clip)
                if checked_input.get('consistent') is not True or checked_input.get('problems'):
                    from novel_manga.repair.policy import RepairDecision
                    decision = RepairDecision('reframe', {'cause': 'request_mismatch',
                        'reason': '；'.join(checked_input.get('problems') or ['当前请求与实际参考图不一致'])
                                  + '\n参考图观察：' + '；'.join(checked_input.get('observations', [])),
                        'request_check': checked_input})
            if decision is None:
                decision = whole_take_decision(precise, history.repeated_errors(directory, cid))
            if decision is None:
                diagnosis = diagnose_numbered(clip_context(directory.parent,ep_names.chapter_of(directory.name),cid, episode_dir=directory))
                repeated = diagnosis.get('cause') == 'generation_mismatch' and history.repeated_errors(directory,cid)
                decision = diagnosed_decision(diagnosis, repeated)
            action, diagnosis = decision.action, decision.diagnosis
            print(f'{cid}: route={action}; {diagnosis.get("cause")}',flush=True)
            if action=='source':
                result = prepare_source_recheck(directory,[cid],instructions={cid:correction(clip)})
                changed.extend(result['changed']);accepted.extend(result['accepted'])
            elif action=='reframe':
                issue = str(verdict.get('feedback') or verdict.get('story_issue') or '按原文修正画面') + '\n判因：' + str(diagnosis.get('reason') or '')
                result, request_checks = reframe_candidate(directory, cid, issue)
                diagnosis['request_checks'] = request_checks
                if cid not in result.get('changed',[]):
                    if result.get('proposal') and not verdict.get('confirmed'):
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
    from novel_manga.application.packing.single_card import single_card_plan
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
