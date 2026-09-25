"""Compose the existing renderer, full repair preparation and review; one shared retry budget."""
from __future__ import annotations
from novel_manga.util import atomic_write_json, read_json
from novel_manga.application.review.episode import review_episode
from novel_manga.application.repair import managed, history
from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint


def _load_current(runner):
    ctx = runner.context
    ctx.clip_plan = read_json(ctx.episode_dir / 'clip_plan.json', {})
    ctx.script = read_json(ctx.episode_dir / 'chapter_script.json', {})
    ctx.feedback = read_json(ctx.episode_dir / 'review_feedback.json', {})
    counts = managed.generated_counts(history.load(ctx.episode_dir))
    ctx._managed_remaining = {c['clip_id']: max(0, managed.generation_limit(ctx.episode_dir, c['clip_id'])-counts.get(c['clip_id'],0))
                              for c in ctx.clip_plan['clips'] if c.get('kind') == 'video'}


def _review(directory, *, fresh):
    from novel_manga.application.review.store import current_takes
    result = review_episode(directory, fresh=fresh)
    history.observe(directory, result, current_takes(directory, read_json(directory / 'clip_plan.json', {}), result))
    return result


def run(runner, *, repair_existing=False):
    ctx = runner.context; directory = ctx.episode_dir
    history.adopt_reviewed_run(directory)
    if repair_existing:
        report = read_json(directory / 'thin_media_report.json', {})
        if not report.get('clips'):
            raise ValueError('repair requires an existing media report')
        if report.get('clip_plan_fingerprint') != plan_fingerprint(ctx.clip_plan):
            raise ValueError('existing media predates this plan; render --review first')
        if report.get('prompt_h3_fingerprint') and report['prompt_h3_fingerprint'] != h3_prompt_fingerprint(ctx.clip_plan):
            raise ValueError('existing media predates this H3 request; render --review first')
    else:
        if not ctx.cache_only:
            ids = {c['clip_id'] for c in ctx.clip_plan['clips'] if c.get('kind') == 'video'}
            history.begin_trial(directory, ids, 'reviewed_render', after_plan=ctx.clip_plan, after_notes=ctx.feedback)
            record = history.load(directory); record['trials'][-1]['managed'] = True; history.save(directory, record)
            _load_current(runner)
        report = runner.run()
        if report.get('status') == 'cache_miss':
            report['quality_review'] = {'passed': False, 'reason': 'cache_miss'}
            return report
    review = _review(directory, fresh=not repair_existing)
    rounds, corrected, blocked = [], [], {}
    for _ in range(managed.MAX_GENERATED_TAKES):
        eligible, blocked = managed.candidates(directory, review)
        if ctx.cache_only or not eligible:
            break
        prepared = managed.prepare(directory, eligible)
        rounds.append(prepared); blocked.update(prepared.get('blocked') or {})
        corrected.extend(prepared.get('changed') or [])
        if prepared.get('skip_render'):
            break
        _load_current(runner)
        report = runner.run()
        review = _review(directory, fresh=False)
    planned = {c['clip_id'] for c in ctx.clip_plan['clips'] if c.get('kind') == 'video'}
    rows = review.get('clips') or {}
    missing = sorted(planned-rows.keys())
    errors = [cid for cid, row in rows.items() if row.get('severity') == 'review_error']
    accepted = (bool((report.get('assembly') or {}).get('thin_passed')) and not report.get('failed_clips')
                and not report.get('gate_failed_clips') and not review.get('feedback') and not missing and not errors)
    result = {'passed': bool(accepted), 'repair_rounds': rounds, 'automatic_corrections': sorted(set(corrected)),
              'blocked': blocked, 'missing_reviews': missing, 'review_errors': errors,
              'remaining': sorted(review.get('feedback') or {}),
              'generated_counts': managed.generated_counts(history.load(directory))}
    report['quality_review'] = result
    atomic_write_json(directory / 'thin_media_report.json', report)
    atomic_write_json(directory / 'episode_execution.json', result)
    return report
