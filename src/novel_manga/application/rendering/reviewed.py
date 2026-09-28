"""Compose the existing renderer, full repair preparation and review; one shared retry budget."""
from __future__ import annotations
from novel_manga.util import read_json
from novel_manga.application.review.execution import review_saved as _review, save_result
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


def run(runner, *, repair_existing=False):
    ctx = runner.context; directory = ctx.episode_dir
    if repair_existing:
        report = read_json(directory / 'thin_media_report.json', {})
        if not report.get('clips'):
            raise ValueError('repair requires an existing media report')
        if report.get('clip_plan_fingerprint') != plan_fingerprint(ctx.clip_plan):
            raise ValueError('existing media predates this plan; render --review first')
        if report.get('prompt_h3_fingerprint') and report['prompt_h3_fingerprint'] != h3_prompt_fingerprint(ctx.clip_plan):
            raise ValueError('existing media predates this H3 request; render --review first')
        if (report.get('review_feedback') or {}) != read_json(directory / 'review_feedback.json', {}):
            raise ValueError('existing media predates these corrections; render --review first')
    else:
        if not ctx.cache_only:
            history.adopt_reviewed_run(directory)
            ids = {c['clip_id'] for c in ctx.clip_plan['clips'] if c.get('kind') == 'video'}
            history.begin_trial(directory, ids, 'reviewed_render', after_plan=ctx.clip_plan, after_notes=ctx.feedback)
            record = history.load(directory); record['trials'][-1]['managed'] = True; history.save(directory, record)
            _load_current(runner)
        report = runner.run()
        if report.get('status') == 'cache_miss':
            report['quality_review'] = {'passed': False, 'reason': 'cache_miss'}
            return report
    if repair_existing:
        history.adopt_reviewed_run(directory)
    review = _review(directory, fresh=not repair_existing)
    rounds, corrected, blocked = [], [], {}
    for _ in range(managed.MAX_GENERATED_TAKES):
        input_blocked = {}
        if report.get('blocked_clips') and not ctx.cache_only:
            prepared_input = managed.prepare_request_conflicts(directory, report)
            input_blocked = prepared_input.get('blocked') or {}
            blocked = input_blocked
            if prepared_input.get('changed'):
                rounds.append(prepared_input)
                corrected.extend(prepared_input['changed'])
                _load_current(runner)
                report = runner.run()
                review = _review(directory, fresh=False)
                continue
        eligible, blocked = managed.candidates(directory, review)
        blocked = {**input_blocked, **blocked}
        if ctx.cache_only or not eligible:
            break
        prepared = managed.prepare(directory, eligible)
        rounds.append(prepared); blocked.update(prepared.get('blocked') or {})
        corrected.extend(prepared.get('changed') or [])
        if prepared.get('skip_render'):
            # Source rechecks can clear a false finding while keeping the take.
            # Use their published verdict instead of closing with the old failure.
            _load_current(runner)
            review = read_json(directory / 'episode_review.json', review)
            report = read_json(directory / 'thin_media_report.json', report)
            break
        _load_current(runner)
        report = runner.run()
        review = _review(directory, fresh=False)
    return save_result(directory, report, review, plan=ctx.clip_plan,
                       rounds=rounds, corrections=corrected, blocked=blocked)
