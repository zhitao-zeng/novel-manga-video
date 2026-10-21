"""Chapter planning IO and orchestration; business modules receive explicit context."""
from __future__ import annotations
from novel_manga.planning.issues import PlanningCode
from novel_manga.planning.context import PlannerContext




from novel_manga.util import atomic_write_json, read_json
from pathlib import Path
import hashlib
import json
import re
import time
import shutil
import novel_manga.planning.constants as pc_constants
import novel_manga.planning.binding as pc_binding
import novel_manga.planning.text as pc_text
import novel_manga.planning.validation as pc_validation
import novel_manga.planning.decisions as pc_decisions
import novel_manga.planning.gate_stats as pc_gate_stats
import novel_manga.application.planning.requests as planner_requests
from novel_manga.planning.methods.blueprint import validate_blueprint
from novel_manga.planning.contracts import validate_outline


from .errors import PlanningInputError
from .run_types import PlanningInputs, PlanningOutcome


def save_method_artifacts(directory, ctx, attempt):
    if not ctx.method_artifacts:
        return
    target = directory / f'method_attempt_{attempt:02d}'
    target.mkdir(exist_ok=True)
    for name, value in ctx.method_artifacts.items():
        atomic_write_json(target / f'{name}.json', value)


def preserve_candidate_history(directory):
    """Keep authoring evidence before the native candidate evaluator reuses its filenames."""
    paths = sorted({p for pattern in ('request_attempt_*', 'response_attempt_*', 'analysis_attempt_*',
        'patch_attempt_*', 'compression_attempt_*', 'method_attempt_*', 'retained_review_attempt_*',
        'planning_attempt.json', 'planning_failed.json', 'chapter_script_report.json')
        for p in directory.glob(pattern)})
    if not paths:
        return
    root = directory/'planning-history'
    number = max((int(p.name) for p in root.glob('*') if p.name.isdigit()), default=0)+1
    target = root/f'{number:04d}'
    target.mkdir(parents=True)
    for path in paths:
        if path.is_dir():
            shutil.copytree(path, target/path.name)
        else:
            shutil.copyfile(path, target/path.name)



def plan_attempts(args, ctx: PlannerContext, inputs: PlanningInputs) -> PlanningOutcome:
    episode = inputs.source.episode
    novel_dir = inputs.source.novel_dir
    episode_dir = inputs.source.episode_dir
    full_bible = inputs.source.full_bible
    profile = inputs.source.profile
    method = inputs.source.method
    existing_draft = inputs.source.existing_draft
    segments = inputs.source.segments
    full_bible = inputs.cast.full_bible
    bible = inputs.cast.bible
    names = inputs.cast.names
    everyone = inputs.cast.everyone
    location_map = inputs.cast.location_map
    grammar = inputs.cast.grammar
    bible = inputs.bible
    names = inputs.names
    location_map = inputs.location_map
    authored = inputs.authored
    schema = inputs.schema
    payload = inputs.payload
    prop_all, prop_names = inputs.prop_all, inputs.prop_names
    started = time.monotonic()
    gate_run = pc_gate_stats.begin_gate_run(novel_dir, episode.index, episode=episode_dir.name)
    attempts: list[dict] = []
    last_issues: list = []  # final verdict is recorded separately from each firing
    repair: dict | None = None
    final_errors: list[str] = []
    result = None
    saved_attempt = read_json(episode_dir / 'planning_attempt.json', {})
    chapter_review = saved_attempt.get('chapter_review')
    same_input = ({k:v for k,v in saved_attempt.get('planning_input', {}).items() if k != 'repair'} == payload)
    resume_review = (not any((getattr(args,'candidate',None), args.replay, existing_draft))
        and same_input and isinstance(saved_attempt.get('candidate'),dict)
        and isinstance(saved_attempt.get('outline'),dict)
        and (chapter_review or {}).get('verdict') == 'incomplete')
    patch_rounds = saved_attempt.get('patch_rounds',0) if resume_review else 0
    patch_seconds_left = saved_attempt.get('patch_seconds_left',pc_constants.PATCH_TOTAL_SECONDS) if resume_review else pc_constants.PATCH_TOTAL_SECONDS
    prior_posture_states = saved_attempt.get('posture_states')
    visibility_review = saved_attempt.get('visibility_review')
    posture_states = None  # Only a reading checked against this draft can be published.
    from novel_manga.application.planning import compression
    compression_rounds = 0
    compression_seconds_left = compression.TOTAL_SECONDS
    def validate_draft(draft):
        nonlocal posture_states, last_issues, visibility_review
        checked = pc_validation.validate_and_normalize(draft, segments, bible, location_map, episode.source_text, everyone, ctx=ctx)
        if not args.replay and all(issue.code == PlanningCode.DURATION_BELOW_MINIMUM for issue in checked.issues):
            from novel_manga.application.planning.posture import check_draft
            def save_pose_progress(states):
                current_attempt.update(candidate=draft, posture_states=states)
                atomic_write_json(episode_dir / 'planning_attempt.json', current_attempt)
            posture_states, posture_issues = check_draft(
                episode_dir, checked.shots, segments, previous=posture_states or prior_posture_states,
                on_progress=save_pose_progress)
            # A later check may fail or be interrupted. Retain this completed reading;
            # fill() still checks its actual shot/source dependencies on the next call.
            current_attempt.update(candidate=draft, posture_states=posture_states)
            atomic_write_json(episode_dir / 'planning_attempt.json', current_attempt)
            checked.issues.extend(posture_issues)
            from novel_manga.application.planning import presence, prop_marks
            candidates = presence.candidates_for(checked.shots, everyone, ctx=ctx)
            try:
                visibility_review = presence.review_visibility(checked.shots, everyone, ctx=ctx,
                    roster={c.name:c.appearance for c in full_bible.characters},
                    props=[p for p in prop_all if p.name in prop_names], previous=visibility_review)
                current_attempt['visibility_review'] = visibility_review
                atomic_write_json(episode_dir / 'planning_attempt.json', current_attempt)
                grades = presence.grades_of(visibility_review,candidates)
                checked.issues.extend(presence.issues_for_grades(checked.shots,grades,everyone))
                marked = prop_marks.interpret(checked.shots,
                    [p for p in prop_all if p.name in prop_names], visibility_review)
                for position,shot in enumerate(checked.shots,1):
                    if position in marked:
                        shot['props'] = marked[position]
            except Exception as error:
                checked.warnings.append(f'report only (在场/道具复核未完成): {type(error).__name__}: {str(error)[:150]}')
        last_issues = checked.issues
        for issue in checked.issues:
            pc_gate_stats.record_gate_event(novel_dir, episode.index, issue.code.code, run_id=gate_run, attempt=attempt)
        return checked

    def compress_duration(draft, checked, attempt):
        nonlocal compression_rounds, compression_seconds_left
        if authored or method or args.replay or getattr(args, 'candidate', None):
            return draft, checked
        feedback = ''
        while (any(i.code == PlanningCode.DURATION_ABOVE_MAXIMUM for i in checked.issues)
               and compression_rounds < compression.ROUNDS and compression_seconds_left > 0):
            compression_rounds += 1
            name = f'compression_attempt_{attempt:02d}_{compression_rounds:02d}'
            atomic_write_json(episode_dir / f'{name}.before.json', draft)
            began = time.monotonic()
            row = {'round': compression_rounds}
            print(json.dumps({'attempt': attempt, 'status': 'compressing', **row}, ensure_ascii=False), flush=True)
            try:
                candidate, details = compression.compress_plan(draft, payload, schema, checked.shots,
                    checked.errors, base_url=args.base_url, model=args.model, max_tokens=args.max_tokens,
                    timeout=min(args.timeout, compression_seconds_left), ctx=ctx, seed=args.seed,
                    feedback=feedback)
                row.update(details)
                atomic_write_json(episode_dir / f'{name}.json', candidate)
                checked = validate_draft(candidate)
                draft = candidate
                row.update(errors_after=checked.errors,
                    after_seconds=round(sum(float(s.get('duration_seconds', pc_text.stage_seconds(s['turns'], ctx=ctx)))
                                            for s in checked.shots), 2))
                feedback = ''
            except ValueError as error:
                # One invalid edit may be corrected inside the same two-edit
                # budget. Keep the original draft and send the concrete failure.
                feedback = str(error)
                row['failed'] = f'ValueError: {feedback[:250]}'
            except Exception as error:
                # A failed compression uses its own budget, not another outline.
                row['failed'] = f'{type(error).__name__}: {str(error)[:250]}'
                break
            finally:
                from novel_manga.resource_waits import active_seconds
                compression_seconds_left = max(0.0, compression_seconds_left - active_seconds(began))
                attempts[-1].setdefault('compressions', []).append(row)
                atomic_write_json(episode_dir / f'{name}.report.json', row)
        return draft, checked

    candidate_path = getattr(args, 'candidate', None)
    if candidate_path or resume_review:
        preserve_candidate_history(episode_dir)
    first_attempt = saved_attempt.get('attempt',1) if resume_review else 1
    final_attempt = first_attempt if candidate_path or resume_review else args.max_redo+1
    for attempt in range(first_attempt, final_attempt+1):
        last_issues = []
        request_payload = {**payload, **({"repair": repair} if repair else {})}
        current_attempt = {'attempt': attempt, 'planning_input': request_payload,
                           'candidate': None, 'outline': None}
        atomic_write_json(episode_dir/'planning_attempt.json', current_attempt)
        atomic_write_json(episode_dir / f"request_attempt_{attempt:02d}.json", request_payload)
        raw_path = episode_dir / f"response_attempt_{attempt:02d}.raw.json"
        if resume_review:
            content = json.dumps(saved_attempt['candidate'],ensure_ascii=False)
            meta = {'resumed_review':True,'analysis':json.dumps(saved_attempt['outline'],ensure_ascii=False)}
        elif existing_draft and attempt == 1:
            content = existing_draft.read_text(encoding='utf-8')
            number = re.search(r'response_attempt_(\d+)', existing_draft.name)
            outline_path = existing_draft.parent / f'analysis_attempt_{number.group(1) if number else "01"}.txt'
            if not outline_path.is_file():
                raise PlanningInputError('压缩已有草稿需要相邻的原始提纲 analysis_attempt_NN.txt')
            outline = outline_path.read_text(encoding='utf-8')
            ctx.outline = json.loads(outline)
            meta = {'loaded_for_compression': str(existing_draft), 'seconds': 0.0,
                    'outline_mode': 'existing_compression', 'outline_attempts': [], 'analysis': outline}
        elif candidate_path and attempt == 1:
            packet = read_json(candidate_path)
            outline = packet.get('outline')
            if not isinstance(outline, dict) or not isinstance(packet.get('draft'), dict):
                raise PlanningInputError('candidate needs a draft and its outline')
            encoded = json.dumps(outline, ensure_ascii=False)
            if method:
                problems = validate_blueprint(encoded, method, payload['segments'])
                if problems:
                    raise PlanningInputError('invalid candidate outline: '+'; '.join(problems))
                ctx.story_blueprint = outline
            else:
                problems = validate_outline(encoded, args.outline_mode,
                    [s['segment_id'] for s in payload['segments']])
                if problems:
                    raise PlanningInputError('invalid candidate outline: '+'; '.join(problems))
            content = json.dumps(packet['draft'], ensure_ascii=False)
            meta = {'candidate_from': str(candidate_path), 'analysis': encoded}
        elif args.replay and attempt == 1:
            content = Path(args.replay).read_text(encoding="utf-8")
            meta = {"replayed_from": str(args.replay)}
            replay = Path(args.replay)
            number = re.search(r'response_attempt_(\d+)', replay.name)
            outline_path = replay.parent / f'analysis_attempt_{number.group(1) if number else "01"}.txt'
            if not outline_path.is_file():
                raise PlanningInputError('replay needs its adjacent analysis_attempt_NN.txt outline')
            outline = outline_path.read_text(encoding='utf-8')
            if method:
                problems = validate_blueprint(outline, method, payload['segments'])
                if problems:
                    raise PlanningInputError('invalid method replay outline: ' + '; '.join(problems))
                ctx.story_blueprint = json.loads(outline)
            else:
                try:
                    ctx.outline = json.loads(outline)
                except ValueError as error:
                    raise PlanningInputError('invalid replay outline: ' + str(error)) from error
        else:
            try:
                content, meta = planner_requests.call_model(base_url=args.base_url, model=args.model, payload=request_payload, schema=schema,
                                           max_tokens=args.max_tokens, timeout=args.timeout, analysis_tokens=args.outline_tokens or (30000 if method else 4096),
                                           notes=args.notes, grammar=grammar, profile=profile,
                                           outline_mode=args.outline_mode, seed=args.seed, ctx=ctx)
            except planner_requests.IncompleteOutlineError as error:
                save_method_artifacts(episode_dir, ctx, attempt)
                final_errors = [str(error)]
                attempts.append({"attempt": attempt, "stage": "outline", "outline_complete": False,
                                 "outline_attempts": error.attempts, "errors": final_errors})
                print(json.dumps({"status": "incomplete_outline", "attempt": attempt, "errors": final_errors}, ensure_ascii=False), flush=True)
                break  # no second pass or outer full-draft retry of an unfinished outline
        save_method_artifacts(episode_dir, ctx, attempt)
        try:
            current_attempt['candidate'] = json.loads(content)
        except ValueError:
            current_attempt['candidate'] = content
        if meta.get('analysis'):
            try:
                current_attempt['outline'] = json.loads(meta['analysis'])
            except ValueError:
                current_attempt['outline'] = meta['analysis']
        atomic_write_json(episode_dir/'planning_attempt.json', current_attempt)
        raw_path.write_text(content, encoding="utf-8")
        if meta.get("analysis"):
            (episode_dir / f"analysis_attempt_{attempt:02d}.txt").write_text(meta.pop("analysis"), encoding="utf-8")
            # Keep the parsed outline where the validator can hold the second pass to it: the
            # retained_dialogue section is a promise, not a suggestion.
            try:
                ctx.outline = json.loads((episode_dir / f"analysis_attempt_{attempt:02d}.txt").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                ctx.outline = {}
        try:
            if re.search(r"\s{2000,}", content):
                raise ValueError("constrained decoding derailed into whitespace (finish_reason=%s)" % meta.get("finish_reason"))
            raw = planner_requests.extract_json(content)
            if authored:
                # What the sheet leaves out is its author's choice, and is recorded as one - by the
                # coverage check, not here: only after validation has moved each citation to the
                # segment its quote is really in does "nobody cites this" mean anything.
                raw = pc_binding.merge(authored, raw, character_names=names)
        except (json.JSONDecodeError, ValueError) as error:
            final_errors = [f"response is not one JSON object: {type(error).__name__}: {error}"]
            attempts.append({"attempt": attempt, **meta, "errors": final_errors})
            repair = pc_decisions.invalid_response_feedback(final_errors, meta.get("finish_reason"))
            continue
        validation = validate_draft(raw)
        errors, warnings, shots = validation.errors, validation.warnings, validation.shots
        fingerprint = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        resent = attempts and attempts[-1].get("fingerprint") == fingerprint
        attempts.append({"attempt": attempt, **meta, "errors": errors, "warnings": warnings, "fingerprint": fingerprint, "resent_previous": bool(resent)})
        print(json.dumps({"attempt": attempt, **meta, "error_count": len(errors), "warning_count": len(warnings)}, ensure_ascii=False), flush=True)
        raw, validation = compress_duration(raw, validation, attempt)
        errors, warnings, shots = validation.errors, validation.warnings, validation.shots
        decision = pc_decisions.decide_validation(validation, final_attempt=attempt == final_attempt,
                    patch_rounds=patch_rounds, patch_seconds_left=patch_seconds_left, allow_floor_waiver=True)
        errors, warnings = decision.errors, decision.warnings
        # A shortfall waived here stays waived while a presence patch runs - that patch is about who is
        # in the picture, not the length.  A shortfall a patch creates is still not waived.
        floor_granted = decision.floor_waived or (
            attempt == final_attempt and any(
                issue.code == PlanningCode.DURATION_BELOW_MINIMUM for issue in validation.issues))
        if decision.floor_waived:
            errors, warnings = decision.errors, decision.warnings
            attempts[-1]["errors"] = errors
            attempts[-1]["floor_waived"] = True
        # A scene-led draft is repaired before projection. Rewriting one legacy
        # stage here would silently diverge from its accepted scene/turn owner.
        patchable = None if candidate_path or ctx.story_blueprint.get('version') == 'scene-screenplay-v2' else decision.targets
        while patchable:
            # Repair local problems in bounded batches, then re-check the draft.
            patch_rounds += 1
            missing_ids, faulty = patchable
            patch_timeout = min(pc_constants.PATCH_TIMEOUT_SECONDS, patch_seconds_left)
            summary = {"round": patch_rounds, "segments": missing_ids, "stages": list(faulty), "timeout_seconds": round(patch_timeout, 1)}
            print(json.dumps({"attempt": attempt, "patch": summary, "status": "patching"}, ensure_ascii=False), flush=True)
            patch_started = time.monotonic()
            patched, batches = None, []
            try:
                # An authored sheet has nothing to insert - what it leaves uncited is recorded as its
                # author's choice by the coverage check - and what a patch rewrites on it keeps the
                # author's columns: the model's answer may correct a quote or a state, never a shot.
                # A stage the patch was asked to split is the one exception: it grows into its
                # stamped parts, and keep_authored accepts exactly that expansion.
                round_split_labels = {issue.stage for issue in validation.issues if issue.code in
                                      {PlanningCode.STAGE_ABOVE_MAXIMUM, PlanningCode.MIXED_THOUGHT}}
                for batch in planner_requests.patch_batches(raw, [] if authored else missing_ids, faulty,
                        segments, names, list(location_map), timeout=patch_timeout,
                        total_timeout=patch_seconds_left, ctx=ctx,
                        split_labels=round_split_labels):
                    details = {key: value for key, value in batch.items() if key != 'draft'}
                    if details:
                        batches.append(details)
                        print(json.dumps({"attempt": attempt, "patch": {**summary, **details}}, ensure_ascii=False), flush=True)
                    if batch.get('failed'):
                        continue
                    candidate = batch['draft']
                    if authored:
                        candidate = pc_binding.keep_authored(raw, candidate, split_labels=round_split_labels)
                    # The current complete proposal survives a later batch failure.
                    atomic_write_json(episode_dir / f'patch_attempt_{attempt:02d}_{patch_rounds:02d}.json', candidate)
                    patched = candidate
                if batches:
                    summary['batches'] = batches
                if patched is None:
                    raise ValueError('no patch batch completed')
            except Exception as error:  # noqa: BLE001 - fall through to the normal redo
                if batches:
                    summary['batches'] = batches
                failure = {**summary, "failed": f"{type(error).__name__}: {str(error)[:120]}", "elapsed_seconds": round(time.monotonic() - patch_started, 1)}
                attempts[-1].setdefault("patches", []).append(failure)
                print(json.dumps({"attempt": attempt, "patch": failure}, ensure_ascii=False), flush=True)
                if patched is None:
                    break
            finally:
                from novel_manga.resource_waits import active_seconds
                patch_seconds_left = max(0.0, patch_seconds_left - active_seconds(patch_started))
            validation = validate_draft(patched)
            patched, validation = compress_duration(patched, validation, attempt)
            errors, warnings, shots = validation.errors, validation.warnings, validation.shots
            attempts[-1].setdefault("patches", []).append({**summary, "errors_after": len(errors), "errors": errors[:6]})
            print(json.dumps({"attempt": attempt, "patch": summary, "error_count": len(errors)}, ensure_ascii=False), flush=True)
            raw = patched  # the next round, or the full redo, starts from the improved draft
            decision = pc_decisions.decide_validation(validation, final_attempt=attempt == final_attempt,
                        patch_rounds=patch_rounds, patch_seconds_left=patch_seconds_left,
                        allow_floor_waiver=floor_granted)
            patchable = decision.targets
        current_attempt.update(candidate=raw,patch_rounds=patch_rounds,patch_seconds_left=patch_seconds_left)
        atomic_write_json(episode_dir/'planning_attempt.json', current_attempt)
        # Whatever the loop could not patch - its rounds ran out, a patch call failed, or this kind of
        # draft takes no patches: a presence disagreement still standing leaves the writer's draft as it
        # is and goes into the report, instead of a full re-plan or a failed chapter.
        settled = pc_decisions.decide_validation(validation, final_attempt=attempt == final_attempt,
                    patch_rounds=pc_constants.PATCH_ROUNDS, patch_seconds_left=0.0, allow_floor_waiver=floor_granted)
        errors, warnings = settled.errors, settled.warnings
        if settled.presence_waived or settled.floor_waived:
            errors, warnings = settled.errors, settled.warnings
            attempts[-1]["errors"] = errors
            if settled.floor_waived:
                attempts[-1]["floor_waived"] = True
            if settled.presence_waived:
                attempts[-1]["presence_waived"] = settled.presence_waived
        if not errors and ctx.strict_plan:
            strict = pc_validation.strict_plan_issues(shots, episode.source_text, segments, raw, ctx=ctx)
            strict_decision = pc_decisions.decide_strict(strict, final_attempt=attempt == final_attempt)
            for issue in strict:
                pc_gate_stats.record_gate_event(novel_dir, episode.index, issue.code.code, run_id=gate_run, attempt=attempt)
            if strict_decision.strict_waived:
                warnings = [*warnings, *strict_decision.warnings]
                attempts[-1]["strict_waived"] = strict_decision.strict_waived
            elif strict_decision.issues:
                last_issues = strict_decision.issues
                errors = strict_decision.errors
                attempts[-1]["errors"] = errors
                print(json.dumps({"attempt": attempt, "strict_errors": errors}, ensure_ascii=False), flush=True)
        if not errors and not authored and not method:
            from novel_manga.application.planning import retention
            section = retention.section_of(ctx.outline)
            evidence_dir = Path(args.replay).parent if args.replay else episode_dir
            evidence_number = (re.search(r'response_attempt_(\d+)', Path(args.replay).name).group(1)
                               if args.replay and re.search(r'response_attempt_(\d+)', Path(args.replay).name)
                               else f'{attempt:02d}')
            evidence_path = evidence_dir / f"retained_review_attempt_{evidence_number}.json"
            try:
                if args.replay:
                    if not evidence_path.is_file():
                        raise PlanningInputError('replay needs its adjacent retained_review_attempt_NN.json')
                    verdict = json.loads(evidence_path.read_text(encoding='utf-8'))
                    expected_review = retention.material(section,shots,episode.source_text,skipped=raw.get('skipped_segments',[]))
                    if verdict.get('policy') != retention.POLICY or verdict.get('inputs') != expected_review:
                        raise ValueError('replay review is not current evidence for this candidate')
                    retained_issues = retention.interpret(verdict, section)
                    current_attempt['chapter_review'] = verdict
                    atomic_write_json(episode_dir/'planning_attempt.json', current_attempt)
                else:
                    chapter_review, retained_issues = retention.review(section,shots,episode.source_text,
                                                                      previous=chapter_review, skipped=raw.get('skipped_segments', []))
                    verdict = chapter_review
                    current_attempt['chapter_review'] = verdict
                    atomic_write_json(episode_dir / 'planning_attempt.json', current_attempt)
                    atomic_write_json(evidence_path, verdict)
            except PlanningInputError:
                raise
            except Exception as error:  # retain the candidate; missing judgment is not a content defect
                from novel_manga.planning.issues import PlanningIssue
                unreviewed = f"{type(error).__name__}: {str(error)[:150]}"
                retained_issues = [PlanningIssue(PlanningCode.REVIEW_INCOMPLETE,unreviewed)]
                current_attempt['chapter_review'] = {'verdict':'incomplete','note':unreviewed}
                atomic_write_json(episode_dir/'planning_attempt.json',current_attempt)
                attempts[-1]['retention_unreviewed'] = unreviewed
            blocking = [i for i in retained_issues if i.code in {
                PlanningCode.SOURCE_CONTRADICTION,PlanningCode.REVIEW_INCOMPLETE}]
            if blocking:
                last_issues = blocking
                content_errors = [i for i in blocking if i.code==PlanningCode.SOURCE_CONTRADICTION]
                attempts[-1]['review_unfinished'] = [i.message for i in blocking if i.code==PlanningCode.REVIEW_INCOMPLETE]
                final_errors = ([i.message for i in content_errors] if content_errors else
                                ['ReviewIncomplete: '+i.message for i in blocking])
                attempts[-1].update(stage='review',errors=final_errors)
                break  # Original Agent budget/local repair, never another whole native draft.
            if retained_issues:
                # Historical/advisory differences do not create a repair action.
                for issue in retained_issues:
                    pc_gate_stats.record_gate_event(novel_dir, episode.index, issue.code.code, run_id=gate_run, attempt=attempt)
                reported = [issue.message for issue in retained_issues]
                warnings = [*warnings, *('report only (保留内容审查): ' + m for m in reported)]
                attempts[-1]["retention_reported"] = reported
        if errors:
            final_errors = errors
            if existing_draft or (not authored and not method and not args.replay
                    and any(i.code == PlanningCode.DURATION_ABOVE_MAXIMUM for i in validation.issues)):
                break
            repair = pc_decisions.revision_feedback(errors, raw, resent=bool(resent))
            continue
        result = (raw, shots, warnings)
        break
    return PlanningOutcome(result, attempts, last_issues, final_errors, posture_states, started, gate_run)
