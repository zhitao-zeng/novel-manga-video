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

from novel_manga.application.repair.history import MAX_GENERATED_TAKES, generated_counts, generation_limit
REFRAME_POLICY = 'reframe-v3-reference-state-and-candidate-feedback'


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
    from novel_manga.application.repair.agent import enabled, FRAME_HANDOFF_POLICY, request_lane, load_config
    runtime=load_config() if enabled(directory) else None
    return {**({'repair_evidence_policy': FRAME_HANDOFF_POLICY, 'request_lane': request_lane(clip),
                'repair_runtime':{**{k:runtime.get(k) for k in ('model','effort','context_window','max_thinking_tokens','repair_extra_body')}, 'tool_choice':{'type':'auto','disable_parallel_tool_use':True}}}
               if runtime is not None else {}),
            **({'final_cut':copy.deepcopy(verdict['final_cut'])} if verdict.get('final_cut') else {}),
            'reframe_policy': REFRAME_POLICY, 'input_check_policy': INPUT_POLICY, 'clip':history.accepted_clip_material(clip),
            'note':inputs.notes.get(clip['clip_id'],''),
            'source':source_state(directory,clip,inputs=inputs),'take':takes.get(clip['clip_id']),
            'error_kinds': (verdict.get('verify') or {}).get('error_kinds', []),
            'request_conflict': (verdict.get('verify') or {}).get('request_conflict', False),
            'evidence':(verdict.get('verify') or {}).get('evidence',verdict.get('story_issue',''))}

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
            if previous.get('status') == 'blocked' and not previous.get('retryable_service_failure'):
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


def prepare_agent_batch(directory: Path, targets: list[str], blocked=None) -> dict:
    """Bounded candidate feedback replaces the old route, under the same video budget."""
    from novel_manga.application.repair import agent
    from novel_manga.application.preparation.request_check import request_consistency
    from novel_manga.application.profiles import plan_fingerprint
    from novel_manga.application.repair.source_recheck import prepare_source_recheck
    blocked = dict(blocked or {})
    changed, accepted = [], []
    decisions = read(directory/'repair_routing.json', {})
    history.adopt_reviewed_run(directory)
    initial_count = len(history.load(directory)['trials'])
    for cid in targets:
        counts = generated_counts(history.load(directory))
        if counts.get(cid, 0) >= generation_limit(directory, cid):
            blocked[cid] = 'effective clip retry budget used'
            continue
        plan = read(directory/'clip_plan.json', {})
        clip = next(c for c in plan['clips'] if c['clip_id'] == cid)
        review = read(directory/'episode_review.json', {})
        verdict = review.get('clips', {}).get(cid, {})
        before = input_state(directory, clip, verdict, current_takes(directory, plan, review))
        previous=decisions.get(cid,{})
        if (previous.get('status')=='blocked' and not previous.get('retryable_service_failure')
                and previous.get('inputs')==before):
            blocked[cid]=previous.get('reason','unchanged rejected repair')
            continue
        source_before = read(directory/'chapter_script.json', {})
        notes_before = read(directory/'review_feedback.json', {})
        proposal_log = None
        candidate_row = None
        def require_current_inputs():
            if (read(directory/'clip_plan.json', {}) != plan
                    or read(directory/'chapter_script.json', {}) != source_before
                    or read(directory/'review_feedback.json', {}) != notes_before
                    or read(directory/'episode_review.json', {}).get('clips',{}).get(cid,{}).get('final_cut') != verdict.get('final_cut')):
                raise ValueError('episode inputs changed while repair agent was running')
        def require_video_budget():
            if generated_counts(history.load(directory)).get(cid,0) >= generation_limit(directory,cid):
                raise ValueError('effective clip retry budget used')
        try:
            audit_path = directory/'work/request_checks'/f'{cid}.agent-proposals-{time.time_ns()}.json'
            proposal_log = {'policy':agent.FRAME_HANDOFF_POLICY, 'clip_id':cid,
                            'candidate_limit':agent.MAX_CANDIDATES,
                            'previous_decision':copy.deepcopy(previous), 'candidates':[]}
            atomic_write_json(audit_path,proposal_log)
            rejections = []
            for revision in range(agent.MAX_CANDIDATES):
                candidate_row = None
                require_current_inputs();require_video_budget()
                candidate_row = {'revision':revision+1,'status':'proposing'}
                proposal_log['candidates'].append(candidate_row)
                atomic_write_json(audit_path,proposal_log)
                try:
                    decision = agent.propose(directory,clip,verdict,**({'rejections':rejections} if rejections else {}))
                    candidate_row.update(candidate=copy.deepcopy(decision),status='proposed')
                    atomic_write_json(audit_path,proposal_log)
                    # Guard both sandbox time and request-check time before any write/adoption.
                    require_current_inputs();require_video_budget()
                    action = decision['action']
                    if action == 'stop':raise ValueError('agent stopped: '+decision['reason'])
                    if action == 'keep':
                        if (verdict.get('final_cut') or {}).get('issues'):
                            raise agent.CandidateRejected(
                                'keep 只复查原生片，不能解除当前实际成片的终审问题；'
                                '请针对已提供的成片证据提出具体输入修复/retake，或有依据地 stop。'
                                '未解决问题：'+str(verdict['final_cut']['issues']),
                                decision,stage='unresolved_final_keep')
                        result = prepare_source_recheck(directory, [cid])
                        candidate_row['recheck_result'] = copy.deepcopy(result)
                        changed.extend(result['changed']);accepted.extend(result['accepted'])
                    else:
                        if action == 'edit':
                            try:
                                proposal = agent.final_proposal(directory,plan,clip,decision)
                            except ValueError as error:
                                raise agent.CandidateRejected(str(error),decision,stage='final_proposal') from error
                            updated = next(c for c in proposal.plan['clips'] if c['clip_id']==cid)
                        else:
                            updated = clip
                        check = agent.check_request(updated,directory.parent)
                        candidate_row['check'] = copy.deepcopy(check)
                        require_current_inputs();require_video_budget()
                        service_error = check.get('error') or (check.get('scope_check') or {}).get('error')
                        if service_error:
                            raise ValueError('agent candidate request check unavailable: '+str(service_error))
                        if check.get('consistent') is not True or check.get('problems'):
                            problems = check.get('problems') or []
                            if not problems:
                                raise ValueError('agent candidate request check returned no verified consistency result')
                            raise agent.CandidateRejected('agent candidate input conflict: '+'; '.join(problems),
                                                          decision,stage='check_request',check=check)
                        if action == 'edit':
                            publish_candidate(directory,proposal,'agent_prompt',{cid})
                        else:
                            publish_retake(directory,retake_proposal(plan,cid,decision))
                        changed.append(cid)
                    candidate_row['status'] = 'adopted' if action!='keep' else 'independently_rechecked'
                    atomic_write_json(audit_path,proposal_log)
                    break
                except agent.CandidateRejected as error:
                    feedback = error.feedback
                    candidate_row.update(candidate=feedback['candidate'],status='rejected',rejection=feedback)
                    atomic_write_json(audit_path,proposal_log)
                    require_current_inputs();require_video_budget()
                    rejections.append(copy.deepcopy(feedback))
                    if revision+1==agent.MAX_CANDIDATES:raise
            decisions[cid] = {'at':time.strftime('%F %T'), 'action':action, 'diagnosis':decision,
                              'status':'prepared', 'inputs':before, 'candidate_audit':str(audit_path)}
        except Exception as error:
            if proposal_log is not None:
                proposal_log['stopped_reason'] = str(error)
                if candidate_row is not None and candidate_row['status']!='rejected':
                    candidate_row.update(status='stopped',reason=str(error))
                atomic_write_json(audit_path,proposal_log)
            blocked[cid] = str(error)[:350]
            decisions[cid] = {'status':'blocked', 'reason':blocked[cid], 'inputs':before,
                              'retryable_service_failure':isinstance(error,agent.AgentUnavailable),
                              **({'candidate_audit':str(audit_path)} if proposal_log is not None else {})}
        atomic_write_json(directory/'repair_routing.json', decisions)
    record=history.load(directory); final_plan=read(directory/'clip_plan.json',{})
    final_notes=read(directory/'review_feedback.json',{})
    for trial in record['trials'][initial_count:]:
        trial.update(managed=True,expected_plan=plan_fingerprint(final_plan),expected_notes=copy.deepcopy(final_notes))
    if len(record['trials'])>initial_count: history.save(directory,record)
    result={'changed':list(dict.fromkeys(changed)), 'accepted':accepted, 'blocked':blocked,
            'service_failures':{cid:blocked[cid] for cid in targets if cid in blocked
                                and decisions.get(cid,{}).get('retryable_service_failure')},
            'skip_render':not changed, 'generated_counts':generated_counts(record)}
    atomic_write_json(directory/'managed_repair_report.json',result)
    return result


def prepare_request_conflicts(directory: Path, report: dict) -> dict:
    """A rejected first request has no video to review, but can use the existing rewrite path."""
    from novel_manga.application.preparation.request_check import current_request
    from novel_manga.application.repair import agent
    if agent.enabled(directory):
        return prepare_agent_batch(directory, [cid for cid, reasons in report.get("blocked_clips", {}).items()
                                              if any(r.startswith("request:") for r in reasons)])
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
    from novel_manga.application.repair import agent
    if agent.enabled(directory):
        return prepare_agent_batch(directory, wanted, blocked)
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
                convert(clip,note=str(final_notes.get(clip['clip_id'],'')),episode_dir=directory)
        atomic_write_json(directory/'clip_plan.json',final_plan)
    for trial in record['trials'][initial_count:]:
        trial.update(managed=True,expected_plan=plan_fingerprint(final_plan),expected_notes=copy.deepcopy(final_notes))
    if len(record['trials'])>initial_count:
        history.save(directory,record)
    result={'changed':list(dict.fromkeys(changed)),'accepted':accepted,'blocked':blocked,
            'skip_render':not changed,'generated_counts':generated_counts(record)}
    atomic_write_json(directory/'managed_repair_report.json',result)
    return result
