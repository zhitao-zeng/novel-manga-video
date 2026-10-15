"""Compose the existing renderer, full repair preparation and review; one shared retry budget."""
from __future__ import annotations
from novel_manga.util import read_json, atomic_write_json
from novel_manga.application.review.execution import review_saved as _review, save_result
from novel_manga.application.repair import managed, history
from novel_manga.application.review import final_cut
from novel_manga.application.profiles import plan_fingerprint, h3_prompt_fingerprint


def _load_current(runner):
    ctx = runner.context
    ctx.clip_plan = read_json(ctx.episode_dir / 'clip_plan.json', {})
    ctx.script = read_json(ctx.episode_dir / 'chapter_script.json', {})
    ctx.feedback = read_json(ctx.episode_dir / 'review_feedback.json', {})
    counts = history.generation_counts(ctx.episode_dir)
    ctx.state.remaining = {c['clip_id']: max(0, managed.generation_limit(ctx.episode_dir, c['clip_id'])-counts.get(c['clip_id'],0))
                              for c in ctx.clip_plan['clips'] if c.get('kind') == 'video'}


def run(runner, *, repair_existing=False, final_review=None):
    from novel_manga.application.repair.agent import request_scope
    settings = getattr(runner.context, 'settings', None)
    lane = ('h3' if getattr(settings, 'local_h3_base_url', '') else
            'sd25' if getattr(settings, 'video_model', '') == 'sd2.5' else None)
    with request_scope(lane):
        return _run(runner, repair_existing=repair_existing, final_review=final_review)


def _run(runner, *, repair_existing=False, final_review=None):
    ctx = runner.context; directory = ctx.episode_dir
    from novel_manga.application.repair.pending_inputs import recover_unrenderable
    if recover_unrenderable(directory):
        from novel_manga.application.review.execution import media_matches
        _load_current(runner)
        repair_existing=media_matches(ctx.clip_plan,read_json(directory/'thin_media_report.json',{}),notes=ctx.feedback)
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
        if not ctx.options.cache_only:
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
    deferred_services = {}
    final_review = final_review or (lambda media: final_cut.review_assembly(runner,media))
    def observe_final():
        nonlocal report,review
        from novel_manga.application.repair.ambience import apply_assembly
        report=apply_assembly(directory,report,context=ctx)
        # The exporter receives an honest current native result, not an old pass.
        report=save_result(directory,report,review,plan=ctx.clip_plan,
                           rounds=rounds,corrections=corrected,blocked=blocked)
        final=final_review(report)
        if final is not None:
            final.setdefault('policy',final_cut.POLICY)
            final.setdefault('context',final_cut.context(directory,report,final['movie']))
            report['final_cut_review']=final
            review=final_cut.apply_feedback(directory,review,final)
            atomic_write_json(directory/'episode_review.json',review)
            atomic_write_json(directory/'final_cut_review.json',final)
            # A blocked/keep preparation reloads this file before returning.
            # Persist both review channels now, rather than reloading an old
            # Qwen-only snapshot after a service failure with no new render.
            report=save_result(directory,report,review,plan=ctx.clip_plan,
                               rounds=rounds,corrections=corrected,blocked=blocked)
    counts=history.generation_counts(directory)
    # The history remains the limit. Allow a final-review round after the native
    # loop, rather than spending that opportunity on an arbitrary loop counter.
    remaining=sum(max(0,managed.generation_limit(directory,c['clip_id'])-counts.get(c['clip_id'],0))
                  for c in ctx.clip_plan['clips'] if c.get('kind')=='video')
    upstream_rounds=2*len([c for c in ctx.clip_plan['clips'] if c.get('kind')=='video'])
    for _ in range(max(1,remaining+1)+upstream_rounds):
        observe_final()
        input_blocked = {}
        if report.get('blocked_clips') and not ctx.options.cache_only:
            pending_report = {**report, 'blocked_clips':{cid: value for cid, value in report['blocked_clips'].items()
                                                       if cid not in deferred_services}}
            prepared_input = managed.prepare_request_conflicts(directory, pending_report)
            deferred_services.update(prepared_input.get('service_failures') or {})
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
        eligible = [cid for cid in eligible if cid not in deferred_services]
        blocked = {**input_blocked, **blocked, **deferred_services}
        if ctx.options.cache_only or not eligible:
            break
        prepared = managed.prepare(directory, eligible)
        deferred_services.update(prepared.get('service_failures') or {})
        rounds.append(prepared); blocked.update(prepared.get('blocked') or {})
        corrected.extend(prepared.get('changed') or [])
        if prepared.get('skip_render'):
            # Source rechecks can clear a false finding while keeping the take.
            # Use their published verdict instead of closing with the old failure.
            _load_current(runner)
            review = read_json(directory / 'episode_review.json', review)
            report = read_json(directory / 'thin_media_report.json', report)
            if prepared.get('assembly_changed') or prepared.get('review_changed'):
                # Re-export the card/audio and run both movie reviewers. No video job.
                continue
            if prepared.get('accepted'):
                # A native keep may correct an erroneous finding, but cannot
                # certify the unchanged delivered movie by itself.
                observe_final()
            break
        _load_current(runner)
        report = runner.run()
        review = _review(directory, fresh=False)
    return save_result(directory, report, review, plan=ctx.clip_plan,
                       rounds=rounds, corrections=corrected, blocked=blocked)
