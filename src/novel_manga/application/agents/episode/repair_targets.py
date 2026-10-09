"""Current repair targets shared by capability checks and actual application."""
from pathlib import Path
from novel_manga.util import read_json
from .arguments import RepairArgumentError
from .input_effects import effects


def eligible_targets(ctx, evidence=None):
    from .evidence import EpisodeEvidence
    evidence=evidence or EpisodeEvidence(ctx)
    plan=read_json(ctx.episode_dir/'clip_plan.json',{})
    report=read_json(ctx.episode_dir/'thin_media_report.json',{})
    review=read_json(ctx.episode_dir/'episode_review.json',{})
    allowed=(set(review.get('feedback')or{})|set(report.get('failed_clips')or[])
        |set(report.get('gate_failed_clips')or[])|set(report.get('blocked_clips')or{})
        |set((report.get('quality_review')or{}).get('remaining')or[])
        |{cid for cid,r in (review.get('clips')or{}).items()if r.get('severity')=='review_error'})
    allowed.update(evidence.unfilmed_clips(plan,report))
    frozen=read_json(ctx.run_dir/'final-candidate/clip_plan.json',{})
    from novel_manga.application.review.execution import media_matches
    if frozen.get('clips') and report and not media_matches(plan,report,
            notes=read_json(ctx.episode_dir/'review_feedback.json',{})):
        from novel_manga.application.profiles import plan_fingerprint,h3_prompt_fingerprint
        old={c['clip_id']:c for c in frozen['clips']}
        allowed.update(c['clip_id']for c in plan.get('clips',[])if c['clip_id']in old and(
            plan_fingerprint({'clips':[c]})!=plan_fingerprint({'clips':[old[c['clip_id']]]})
            or h3_prompt_fingerprint({'clips':[c]})!=h3_prompt_fingerprint({'clips':[old[c['clip_id']]]})))
    if evidence.clip_technical_issues(report):
        from novel_manga.application.review.execution import media_matches
        if media_matches(plan,report,notes=read_json(ctx.episode_dir/'review_feedback.json',{})):
            allowed.update(c['clip_id']for c in plan.get('clips',[])if c.get('kind')=='video')
    if (review.get('review_mode')=='verify' and evidence.current_review(review)
            and (report.get('assembly')or{}).get('pending_publish')):
        from novel_manga.application.repair.delivery import precise_review_blockers
        from novel_manga.application.review.store import current_takes
        allowed.update(precise_review_blockers(review,current_takes(ctx.episode_dir,plan,review),
            [c['clip_id']for c in plan.get('clips',[])if c.get('kind')=='video']))
    return allowed


def verified_input(ctx,row):
    from .bounded_recovery import evidence
    candidate=row
    if row.get('resume'):
        from .input_records import latest_input_records
        candidate=latest_input_records(ctx.episode_dir).get(row['clip_id'],(None,{}))[1].get('decision')or{}
    proof=evidence(ctx,row['clip_id'])
    return bool(proof and effects(candidate)==effects(proof['candidate'])
        and candidate.get('source_quote')==proof['candidate'].get('source_quote'))


def validate_target(ctx,row,*,allowed=None,evidence=None):
    from .evidence import EpisodeEvidence
    evidence=evidence or EpisodeEvidence(ctx)
    cid=row['clip_id'];allowed=eligible_targets(ctx,evidence)if allowed is None else allowed
    if row.get('action')in {'edit','retake'} and cid in evidence.voice_recording_blocks(
            read_json(ctx.episode_dir/'clip_plan.json',{})):
        raise RepairArgumentError('independent voice recording failed; choose stop or finish')
    if cid in allowed or row.get('action')=='input' and verified_input(ctx,row):return
    if row.get('action')=='keep':
        from novel_manga.review.storage import take_identity
        review=read_json(ctx.episode_dir/'episode_review.json',{})
        verdict=(review.get('clips')or{}).get(cid,{})
        report=read_json(ctx.episode_dir/'thin_media_report.json',{})
        take=next((r.get('selected')or{}for r in report.get('clips',[])if r['clip_id']==cid),{})
        video=Path(take['video'])if take.get('video')else None
        if (evidence.current_review(review) and verdict.get('severity')in {'pass','minor'}
                and take.get('passed')is True and video and video.is_file()
                and verdict.get('take')==take_identity(video)):return
    raise RepairArgumentError(f'{cid} has no eligible unresolved issue or verified input candidate; '
        'choose an eligible target: '+', '.join(sorted(allowed)))
