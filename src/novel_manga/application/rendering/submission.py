#!/usr/bin/env python3
from __future__ import annotations
from novel_manga.media import generation

import time
from pathlib import Path


from novel_manga.media.common import log
from novel_manga.media.asset_repair import wait_for_inflight_redraws
from novel_manga.media.resources import acquire_inflight_slot, release_inflight_slot
from novel_manga.media.policy import PRESCREEN_RISK, SUBMIT_BACKOFF_SECONDS
from novel_manga.media import generation, cache
from novel_manga.media.cache import CacheMiss
from novel_manga.media.policy import resubmittable
from novel_manga.util import atomic_write_json, media_duration

from novel_manga.application.profiles import plan_fingerprint

POLICY = "thin-media-v22-coverage-gate"
CHAT_CONTEXT_MESSAGES = 2   # earlier messages shown above the new ones on a chat card
CHAT_HISTORY_EPISODES = 3   # how far back to look for them


# ---- subtitle helpers (v16) ----


MAX_CER = 0.5          # kept in the report; no longer gates


# An English prompt (local H3) gets its retake note in English: H3 speaks what is written in Chinese, and
# read the note above out as dialogue - 雾月 58, 59, 97, 1262 and 1736 kept such takes.



from . import requests


def generate_clip(ctx, clip: dict, attempt: int) -> Path:
    preparation_started = time.monotonic()
    if not ctx.options.cache_only:
        from novel_manga.application.preparation.readiness import reference_issues
        reasons = ctx.state.blocked_clips.get(clip["clip_id"], []) or reference_issues(clip, ctx.novel_dir)
        if reasons:
            if not hasattr(ctx.state, 'blocked_clips'):
                ctx.state.blocked_clips = {}
            ctx.state.blocked_clips[clip["clip_id"]] = reasons
            raise RuntimeError("request blocked before generation: " + "; ".join(reasons))
    ctx.state.clip(clip['clip_id']).generated = False  # set once a new video is actually made: process_clip counts only those
    directory = ctx.work / "clips" / clip["clip_id"] / f"attempt_{attempt:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "clip.mp4"
    episode_dir = ctx.work.parent
    request, references, digests, retry = generation.build_request(ctx, clip, attempt)
    prompt = request['prompt']
    import novel_manga.application.repair.history as repair_history
    cached = cache.current_video(ctx, clip, attempt, directory, references, digests, repair_history)
    if cached is not None:
        if not ctx.options.cache_only:
            repair_history.complete_generation(episode_dir, clip['clip_id'], cached)
        return cached
    if ctx.options.first_pass_only:
        from novel_manga.application.preparation.store import unfilmed_video_clips
        if clip['clip_id'] not in unfilmed_video_clips(episode_dir, {'clips': [clip]}):
            raise ValueError('first pass defers retakes of existing video to the repair queue')
    if requests.prescreens(ctx, clip):
        ctx.state.clip(clip['clip_id']).prescreened = True
        risk = requests.prescreen_prompt(prompt)
        if risk >= PRESCREEN_RISK:
            ctx.state.clip(clip['clip_id']).softened = True
            log(f"{clip['clip_id']}: prescreen risk {risk:.2f}; softening the wording before the first submission")
            prompt = requests.clip_prompt(ctx, clip) + retry
            request["prompt"] = prompt
    # Earlier runs (or the pre-v11 privacy retry) may hold the matching video
    # under another attempt directory; use it rather than paying again.
    cached = cache.other_video(ctx, clip, attempt, directory, references, digests)
    if cached is not None:
        if not ctx.options.cache_only:
            repair_history.complete_generation(episode_dir, clip['clip_id'], cached)
        return cached
    if ctx.options.cache_only:
        raise CacheMiss(f"{clip['clip_id']} attempt {attempt}: not in the cache")
    if requests.english_correction_for_new_take(ctx, clip):
        return generate_clip(ctx, clip, attempt)
    if ctx.settings.local_h3_base_url and not ctx.options.cache_only:
        # About to submit a NEW request on an H3 lane: the caches above did not hold.  Two ways
        # to submit the wrong words - clip_base falls back to the Chinese prompt when there is
        # no current English one (H3 then recites stage directions as dialogue), or uses an
        # English one made from an earlier Chinese prompt (a re-pack or a new correction).  The
        # correction gate above only guarded takes that carried a note; this closes the gap for
        # every entry point.  Old passed caches never reach here - they returned above.
        from novel_manga.application.profiles import h3_prompt_outdated
        note = str(ctx.feedback.get(clip['clip_id']) or '').strip()
        if not generation.uses_h3_prompt(ctx, clip) or h3_prompt_outdated(clip, note, strict=True):
            why = ('no English prompt yet' if not clip.get('prompt_h3')
                   else 'prompt_h3_skip is set on a clip that has no matching cache to reuse'
                   if clip.get('prompt_h3_skip')
                   else 'its English prompt predates the current Chinese one (re-packed or re-corrected)')
            raise RuntimeError(f"{clip['clip_id']}: H3 lane cannot submit a new request from stale words ({why}); "
                               "run scripts/build_h3_prompts.py for this chapter first")
    if generation.uses_h3_prompt(ctx, clip):
        from novel_manga.story.h3 import request_issues
        contradictions = request_issues(clip)
        if contradictions:
            if not hasattr(ctx.state, 'blocked_clips'):
                ctx.state.blocked_clips={}
            ctx.state.blocked_clips[clip['clip_id']]=['request: '+issue for issue in contradictions]
            raise ValueError('H3 request needs identity repair: '+'; '.join(contradictions))
    task_path = output.with_suffix(output.suffix + ".task.json")
    from novel_manga.util import read_json
    saved_request=read_json(output.parent/'request.json',{})
    resume_scope=bool(saved_request.get('_repack_budget') and read_json(task_path,{}).get('task_id')
        and cache.request_matches(ctx,clip,saved_request,references,digests))
    remaining=ctx.state.remaining.get(clip['clip_id'])
    needs_extra = not resume_scope and ((remaining is not None and remaining <= 0) or repair_history.remaining_takes(episode_dir,clip['clip_id'])<=0)
    if needs_extra and not repair_history.extra_take_ready(episode_dir, clip['clip_id']):
        if repair_history.conditional_extra(episode_dir, clip['clip_id']):
            raise ValueError('extra take requires a validated edit/input/retake and remaining authorization; '
                             'a risk-only acceptance does not activate it')
        raise ValueError('effective clip retry budget used; current cached take retained')
    wait_for_inflight_redraws(references)
    if ctx.options.prepared_requests_only:
        request, references, digests, retry = generation.build_request(ctx, clip, attempt)
    if generation.uses_h3_prompt(ctx, clip):
        from novel_manga.application.preparation.request_check import before_generation
        before_generation(ctx, clip, request)
    if needs_extra:
        if not generation.uses_h3_prompt(ctx, clip) or not repair_history.activate_extra_take(episode_dir, clip['clip_id']):
            raise ValueError('reviewed extra-take authorization unavailable after input preflight')
        ctx.state.remaining[clip['clip_id']] = max(0, repair_history.generation_limit(
            episode_dir, clip['clip_id']) - repair_history.generation_counts(episode_dir).get(clip['clip_id'], 0))
    task_path = output.with_suffix(output.suffix + ".task.json")
    from novel_manga.util import read_json
    prior_task = read_json(task_path, {}).get('task_id')
    request = repair_history.generation_request(episode_dir, output, request,
        expected_plan=plan_fingerprint(ctx.clip_plan), expected_notes=ctx.feedback)
    atomic_write_json(directory / "request.json", request)
    log(f"{clip['clip_id']} attempt {attempt}: requesting {clip['request_seconds']}s video with {len(references)} references")
    started = time.monotonic()
    slot = acquire_inflight_slot(ctx.novel_dir, ctx.options.inflight)
    admitted = time.monotonic()
    timings = {'started_at': time.time()-(admitted-preparation_started),
               'preparation_s': round(started-preparation_started, 3),
               'admission_wait_s': round(admitted-started, 3)}
    try:
      for wait in (*SUBMIT_BACKOFF_SECONDS, None):
        try:
            request=repair_history.reserve_scope_submission(episode_dir,output,{**request,'clip_id':clip['clip_id']})
            generation.submit(ctx, clip, request, output, references)
            break
        except RuntimeError as error:
            repair_history.finish_scope_submission(episode_dir,output,outcome='not_accepted' if resubmittable(error) else 'unknown')
            # Throttled at submission (higher --parallel): wait and resubmit
            # instead of failing the clip; content errors propagate at once.
            # An unconfirmed submission may already be a paid task: never resent from here.
            if wait is None or not resubmittable(error):
                raise
            log(f"{clip['clip_id']} attempt {attempt}: video service throttled or H3 pool full ({str(error)[:80]}); retrying in {wait}s")
            time.sleep(wait)
    finally:
        release_inflight_slot(slot)
        receipt = read_json(task_path, {})
        timings.update(provider_elapsed_s=round(time.monotonic()-admitted, 3),
                       video_present=output.is_file(),
                       provider=receipt.get('resume_timing') or receipt.get('client_timing') or {},
                       service=receipt.get('service_timing') or {})
        atomic_write_json(directory/'generation-timing.json', timings)
    # A resumed task may have used an older sampler than today's target.
    # generation.submit retained its actual parameters without changing ownership.
    request={**request,**({'_repack_budget':read_json(directory/'request.json',{}).get('_repack_budget')} if read_json(directory/'request.json',{}).get('_repack_budget') else {})}
    atomic_write_json(directory / 'request.json', request)
    current_task = read_json(task_path, {}).get('task_id')
    owner = request.get(repair_history.GENERATION_FIELD) or {}
    if current_task and current_task != prior_task and (not owner or owner.get('take')):
        # The provider discarded an old legacy task and really submitted a
        # new one. That new task belongs to the current managed trial.
        request = repair_history.generation_request(episode_dir, output, request,
            expected_plan=plan_fingerprint(ctx.clip_plan), expected_notes=ctx.feedback, fresh_task=True)
        atomic_write_json(directory / "request.json", request)
    ctx.state.clip(clip['clip_id']).generated = not (prior_task and current_task == prior_task
                               and not request.get(repair_history.GENERATION_FIELD))
    seconds = media_duration(output)
    completion = repair_history.complete_generation(episode_dir, clip['clip_id'], output, duration=seconds)
    if remaining is not None:
        if completion is not None:
            counts = repair_history.generation_counts(episode_dir)
            ctx.state.remaining[clip['clip_id']] = repair_history.remaining_takes(episode_dir,clip['clip_id'])
        elif ctx.state.clip(clip['clip_id']).generated:
            ctx.state.remaining[clip['clip_id']]-=1
    finished = time.monotonic()
    log(f"{clip['clip_id']} attempt {attempt}: video ready in {finished - started:.0f}s "
        f"(admission {timings['admission_wait_s']:.0f}s, "
        f"instance wait {timings['provider'].get('pool_wait_s', 0):.0f}s, "
        f"provider {timings['provider_elapsed_s']:.0f}s, {seconds:.1f}s long)")
    repair_history.finish_scope_submission(episode_dir,output,outcome='completed')
    return output
