"""RepairOperations: existing episode operations with explicit service dependencies."""
from __future__ import annotations

import copy
import os
from pathlib import Path

from novel_manga.util import atomic_write_json, read_json
from .arguments import RepairArgumentError
from novel_manga.application.production.artifacts import media_started


from .tool_inputs import input_file
from .tool_state import record_state


class RepairOperations:
    def __init__(self, ctx, drafts, evidence):
        self.ctx = ctx
        self.drafts = drafts
        self.evidence = evidence

    def apply(self, payload):
        from novel_manga.application.repair import history
        answer = input_file(self.ctx, payload, 'output/repair.json')
        if not isinstance(answer, dict) or not isinstance(answer.get('decisions'), list):
            raise ValueError('repair requires a decisions array')
        if any(not isinstance(row, dict) or not isinstance(row.get('clip_id'), str)
               for row in answer['decisions']):
            raise ValueError('each repair decision requires an object and clip_id')
        if len({row['clip_id'] for row in answer['decisions']}) != len(answer['decisions']):
            raise ValueError('repair decision must name each eligible clip at most once')
        if any(row.get('action') == 'crop' for row in answer['decisions']):
            if len(answer['decisions']) != 1:
                raise ValueError('submit one media crop separately')
            from .media_repair import apply
            result = apply(self.ctx,answer['decisions'][0])
            result.update(submission_id=payload.get('submission_id'),submitted_decisions=answer['decisions'])
            path=self.ctx.run_dir/'tool-repair-decisions.json'
            rounds=read_json(path,[]);rounds.append(result);atomic_write_json(path,rounds)
            record_state(self.ctx,'media_repaired',media_changed=result['media_changed'])
            return {**result,'generation_counts':history.generation_counts(self.ctx.episode_dir)}
        if any(row.get('action') in {'repair_assets','repack'} for row in answer['decisions']):
            if len(answer['decisions'])!=1:raise ValueError('submit one upstream operation separately')
            row=answer['decisions'][0]
            if row['action']=='repair_assets':
                from . import upstream_assets
                result=upstream_assets.prepare(self.ctx,row)
                if result.get('asset'):result=upstream_assets.apply_to_snapshot(self.ctx,result)
                if result.get('input_decision'):
                    from .bounded_recovery import evidence,grant
                    from .input_records import _standard_input_route
                    candidate=result['input_decision']
                    try:_standard_input_route(self.ctx,row['clip_id'],candidate)
                    except ValueError:
                        proof=evidence(self.ctx,row['clip_id'])
                        if proof:grant(self.ctx,row['clip_id'],proof,kind='input')
            else:
                from .scoped_repack import apply
                result=apply(self.ctx,row)
            result.update(upstream_action=row['action'],submission_id=payload.get('submission_id'),submitted_decisions=answer['decisions'])
            path=self.ctx.run_dir/'tool-repair-decisions.json';rounds=read_json(path,[]);rounds.append(result);atomic_write_json(path,rounds)
            record_state(self.ctx,'upstream_'+row['action'],changed=result.get('changed',[]),kind=result.get('kind'))
            return result
        if any(row.get('action') in {'replan','recheck_input'} for row in answer['decisions']):
            if len(answer['decisions'])!=1:
                raise ValueError('submit one editorial operation separately from clip patches')
            from novel_manga.application.agents.episode.pipeline_preparation import editorial_request
            return editorial_request(self.ctx,answer['decisions'][0])
        if (self.ctx.input_mode == 'source' and not (self.ctx.run_dir/'prepared.json').exists()
                and not media_started(self.ctx.episode_dir)):
            from .draft_assets import repair
            if any(row.get('action') != 'input' for row in answer['decisions']):
                raise ValueError('edit the provisional draft directly; apply accepts asset input requests before adoption')
            results, blocked = [], {}
            for row in answer['decisions']:
                try:
                    results.append(repair(self.ctx, row))
                except Exception as error:
                    blocked[row['clip_id']] = str(error)
            return {'ok': not blocked, 'changed': [], 'input_repaired': [], 'blocked': blocked,
                    'assets_added': list(dict.fromkeys(x for r in results for x in r['assets_added'])),
                    'assets': [x for r in results for x in r['assets']],
                    'next': 'Read new images in input/catalog.json and bind their IDs in the draft before render.'}
        review = read_json(self.ctx.episode_dir / 'episode_review.json', {})
        report = read_json(self.ctx.episode_dir / 'thin_media_report.json', {})
        from .repair_targets import eligible_targets,validate_target
        allowed = eligible_targets(self.ctx,self.evidence)
        counts = history.generation_counts(self.ctx.episode_dir)
        if os.environ.get('NOVEL_FIRST_PASS_ONLY') == '1':
            from .arguments import first_pass_scope
            pending = first_pass_scope(self.ctx,[row['clip_id'] for row in answer['decisions']])
            allowed &= pending
        voice_blocks = self.evidence.voice_recording_blocks(read_json(self.ctx.episode_dir / 'clip_plan.json', {}))
        voice_blocked = {row['clip_id']: voice_blocks[row['clip_id']] for row in answer['decisions']
                         if row.get('action') in {'edit', 'retake'} and row['clip_id'] in voice_blocks}
        if voice_blocked:
            return {'ok': False, 'error': 'voice_recording_blocked', 'changed': [],
                    'voice_recording_blocked': voice_blocked, 'blocked': voice_blocked,
                    'generation_counts': counts}
        from .arguments import spent_video_budget
        exhausted = spent_video_budget(self.ctx,[row['clip_id'] for row in answer['decisions']
            if row.get('action') in {'edit','retake','input','accept_risk'}])
        from .bounded_recovery import input_operation
        from .repair_targets import verified_input
        for row in answer['decisions']:
            if row.get('action')=='input' and(input_operation(self.ctx,row['clip_id'],row)['allowed']
                    or verified_input(self.ctx,row)):
                exhausted.pop(row['clip_id'],None)
        # A spent clip must not veto a different clip's permitted repair.
        answer = {**answer, 'decisions': [row for row in answer['decisions'] if row['clip_id'] not in exhausted]}
        input_rows = [row for row in answer['decisions'] if row.get('action') == 'input']
        input_changed, input_handled, input_blocked, repaired = [], set(), {}, []
        input_reused = []
        input_argument_errors = {}
        if input_rows:
            from .input_repair import repair, effects, normalize_arguments
            handled_effects = []
            reused_effects = []
            for row in input_rows:
                cid = row['clip_id']
                if cid in input_handled:
                    raise ValueError('input decision must name each eligible clip at most once')
                input_handled.add(cid)
                try:
                    row = normalize_arguments(row)
                    validate_target(self.ctx,row,allowed=allowed,evidence=self.evidence)
                    if not row.get('resume') and effects(row) in handled_effects:
                        repaired.append(cid)
                        if effects(row) in reused_effects:
                            input_reused.append(cid)
                        continue
                    if not row.get('reason'):
                        raise ValueError('input repair needs a reason')
                    result = repair(self.ctx, row)
                    input_changed.extend(result['changed'])
                    repaired.extend(result['input_repaired'])
                    if result.get('reused'):
                        input_reused.extend(result['input_repaired'])
                        reused_effects.append(effects(row))
                    handled_effects.append(effects(row))
                except Exception as error:
                    input_blocked[cid] = str(error)
                    if isinstance(error, RepairArgumentError):
                        input_argument_errors[cid] = str(error)
        deferred = [row['clip_id'] for row in answer['decisions']
                    if row.get('action') != 'input' and row['clip_id'] in input_changed]
        ordinary = {'decisions': [row for row in answer['decisions']
                    if row.get('action') != 'input' and row['clip_id'] not in input_changed]}
        outcome = self.apply_decisions(ordinary, allowed-input_handled-set(input_changed))
        outcome['changed'] = list(dict.fromkeys([*input_changed, *outcome['changed']]))
        outcome['blocked'].update(input_blocked)
        outcome['blocked'].update(exhausted)
        if input_argument_errors:
            outcome.setdefault('argument_errors', {}).update(input_argument_errors)
        outcome['submission_id'] = payload.get('submission_id')
        outcome['submitted_decisions'] = input_file(self.ctx, payload, 'output/repair.json')['decisions']
        outcome['input_repaired'] = list(dict.fromkeys([*repaired, *outcome.get('risk_accepted', [])]))
        if input_reused:
            outcome['input_reused'] = list(dict.fromkeys(input_reused))
        # Merely accepting a soft input risk is not an extra-take repair plan.
        history.mark_extra_prepared(self.ctx.episode_dir, set(outcome['changed']) | set(repaired))
        if input_rows:
            from .input_repair import repair_status
            outcome['input_repairs'] = repair_status(self.ctx)
        if deferred:
            outcome['deferred'] = deferred
            outcome['deferred_reason'] = 'Input repair changed these requests; inspect the updated evidence before editing their old prompts.'
        path = self.ctx.run_dir / 'tool-repair-decisions.json'
        rounds = read_json(path, [])
        rounds.append(outcome)
        atomic_write_json(path, rounds)
        record_state(self.ctx, 'repair_applied', changed=outcome['changed'], blocked=outcome['blocked'])
        return {'ok': not outcome['blocked'], **outcome,
                'generation_counts': history.generation_counts(self.ctx.episode_dir)}


    def apply_decisions(self, answer, allowed):
        """Apply the agent's one candidate through existing publication and keep review."""
        from novel_manga.application.agents.episode.contracts import validate_patch
        from .requests import compose_repair
        from novel_manga.application.repair import history
        from novel_manga.application.repair.publication import publish_candidate
        from novel_manga.application.review.execution import recheck_current_clip
        from novel_manga.repair.proposal import RepairProposal
        from novel_manga.application.profiles import h3_stamp
        directory = self.ctx.episode_dir
        plan = read_json(directory / 'clip_plan.json')
        candidate = copy.deepcopy(plan)
        blocked, changed, kept, keep_checks, risk_accepted = {}, [], [], [], []
        argument_errors = {}
        from novel_manga.review.storage import take_identity
        review = read_json(directory/'episode_review.json', {})
        current_review = self.evidence.current_review(review)
        media = read_json(directory/'thin_media_report.json', {})
        selected = {r['clip_id']: r.get('selected') or {} for r in media.get('clips', [])}
        seen = set()
        for row in answer.get('decisions', []):
            cid = row.get('clip_id')
            if cid in seen:
                raise ValueError('repair decision must name each eligible clip at most once')
            seen.add(cid)
            verdict = (review.get('clips') or {}).get(cid, {})
            take = selected.get(cid, {})
            video = Path(take['video']) if take.get('video') else None
            if row.get('action') == 'keep' and (verdict.get('final_cut') or {}).get('issues'):
                blocked[cid] = ('native keep cannot resolve current final-cut findings; '
                                'verify the supplied movie frames and propose a compliant edit or stop')
                continue
            if (row.get('action') == 'keep' and current_review
                    and verdict.get('severity') in {'pass', 'minor'} and cid not in (review.get('feedback') or {})
                    and take.get('passed') is True and video and video.is_file()
                    and verdict.get('take') == take_identity(video)):
                kept.append(cid)
                continue
            if cid not in allowed:
                blocked[cid] = 'clip has no eligible failure or current verified take to keep'
                continue
            clip = copy.deepcopy(next(c for c in candidate['clips'] if c['clip_id'] == cid))
            try:
                action = row['action']
                if not row.get('reason'):
                    raise ValueError('repair decision needs a reason')
                if action == 'keep':
                    if not video or not video.is_file():
                        raise ValueError('no current video to keep; use input to fix references or accept_risk for an explicit pre-render reference risk')
                    limit = history.recheck_limit(directory, cid)
                    prior = read_json(self.ctx.run_dir/'tool-repair-decisions.json', [])
                    if limit is not None and sum(cid in r.get('keep_checks', []) for r in prior) >= int(limit):
                        raise ValueError('independent recheck allowance used; stop unresolved clip')
                    keep_checks.append(cid)
                    checked = recheck_current_clip(directory, cid)
                    kept.extend(checked['accepted'])
                    if not checked['accepted']:
                        blocked[cid] = 'current video independent recheck still failed'
                elif action == 'accept_risk':
                    from novel_manga.application.preparation.readiness import accept_render_risks
                    if accept_render_risks(directory, clip, row.get('risks'), row['reason']):
                        risk_accepted.append(cid)
                elif action in {'edit', 'retake'}:
                    if action == 'edit':
                        prompt = self.drafts.edit_prompt(clip,row)
                        if prompt.strip() == (clip.get('prompt_h3') or '').strip():
                            raise ValueError('agent edit made no request change')
                        clip['prompt_h3'] = prompt.strip()
                    elif not clip.get('prompt_h3'):
                        raise ValueError('clip has no English request; use edit to supply h3 directions or prompt_h3 before retake')
                    clip['repair_take'] = int(clip.get('repair_take', 0)) + 1
                    notes = read_json(directory / 'review_feedback.json', {})
                    clip['prompt_h3_of'] = h3_stamp(clip, notes.get(cid, ''))
                    clip['prompt_h3_agent'] = {
                        'run': self.ctx.run_dir.name, 'reason': row['reason'],
                        'session': read_json(self.ctx.work_dir / 'episode-session.json', {})}
                    candidate['clips'] = [clip if c['clip_id'] == cid else c for c in candidate['clips']]
                    changed.append(cid)
                elif action == 'stop':
                    blocked[cid] = row['reason']
                else:
                    raise ValueError('unknown repair action')
            except (ValueError, KeyError) as error:
                blocked[cid] = str(error)
                if isinstance(error, RepairArgumentError):
                    argument_errors[cid] = str(error)
        unresolved_clips=sorted(allowed-seen)
        if changed:
            proposal = RepairProposal(
                result={'changed': changed}, script=read_json(directory / 'chapter_script.json'),
                plan=candidate, notes=read_json(directory / 'review_feedback.json', {}),
                changes={cid: next(r for r in answer['decisions'] if r['clip_id'] == cid) for cid in changed})
            publish_candidate(directory, proposal, 'episode_agent', changed)
            record = history.load(directory)
            record['trials'][-1]['managed'] = True
            history.save(directory, record)
        return {'changed': changed, 'accepted': kept, 'blocked': blocked, 'risk_accepted': risk_accepted,
                'unresolved_clips':unresolved_clips,
                **({'argument_errors': argument_errors} if argument_errors else {}),
                **({'keep_checks': keep_checks} if keep_checks else {})}
