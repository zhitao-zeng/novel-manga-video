#!/usr/bin/env python3
from __future__ import annotations
from novel_manga.media.issues import QualityIssue

from pathlib import Path


from novel_manga.media.common import log
from novel_manga.media import cache
from novel_manga.media import analysis as media_analysis
from novel_manga.util import atomic_write_json


POLICY = "thin-media-v22-coverage-gate"
CHAT_CONTEXT_MESSAGES = 2   # earlier messages shown above the new ones on a chat card
CHAT_HISTORY_EPISODES = 3   # how far back to look for them


# ---- subtitle helpers (v16) ----


MAX_CER = 0.5          # kept in the report; no longer gates


# An English prompt (local H3) gets its retake note in English: H3 speaks what is written in Chinese, and
# read the note above out as dialogue - 雾月 58, 59, 97, 1262 and 1736 kept such takes.





def analyse_clip(ctx, clip: dict, video: Path) -> dict:
    from novel_manga.application.profiles import speech_gate_result
    from novel_manga.story.voice_delivery import postmixed, visual_request
    sampling = cache.sampling_report(ctx, video)
    if postmixed(clip):
        visual = visual_request(clip)
        raw = media_analysis.analyse_clip(ctx, visual, video)
        raw = check_clip_black(ctx, visual, raw, video)
        if not raw['passed']:
            return {**raw, **sampling}
        from novel_manga.media.inner_voice import mix
        mixed = mix(ctx, clip, video)
        final = media_analysis.analyse_clip(ctx, clip, mixed)
        return {**speech_gate_result(ctx.novel_dir, final, ctx.episode_dir),
                **sampling, 'video': str(video), 'postmix_video': str(mixed), 'audio_source': 'postmixed_inner_voice'}
    raw = media_analysis.analyse_clip(ctx, clip, video)
    return {**speech_gate_result(ctx.novel_dir, check_clip_black(ctx, clip, recheck_speech(ctx, clip, raw, video), video), ctx.episode_dir),
            **sampling}


def recheck_speech(ctx, clip: dict, analysis: dict, video: Path) -> dict:
    return media_analysis.recheck_speech(ctx, clip, analysis, video)


def check_clip_black(ctx, clip: dict, analysis: dict, video: Path) -> dict:
    """Targeted black-frame recovery must reject a black take before assembly."""
    if clip['clip_id'] not in ctx.state.black_checks or analysis.get('black_check_policy') == 3:
        return analysis
    from novel_manga.application.repair.recovery import black_ranges, brighten_dark_scene
    ranges = black_ranges(video, 0.2)
    # Use the same one-second threshold as final-media QC.
    issues = [issue for issue in analysis.get('issues') or [] if issue != QualityIssue.BLACK_FRAMES.code]
    exposure = False
    if ranges and any(end - start >= 1.0 for start, end in ranges):
        exposure = brighten_dark_scene(video, [(a,b) for a,b in ranges if b-a >= 1.0], ctx.episode_dir, clip['clip_id'])
        if exposure:
            ranges = []
            log(f"{clip['clip_id']}: restored detail in an underexposed scene; original archived, audio unchanged")
    if any(end - start >= 1.0 for start, end in ranges):
        issues.append(QualityIssue.BLACK_FRAMES.code)
    result = {**analysis, 'black_checked': True, 'black_check_policy': 3, 'black_ranges': ranges, 'issues': issues, 'passed': not issues,
              **({'exposure_gamma': 1.6} if exposure else {})}
    atomic_write_json(video.parent / 'asr.json', result)
    return result
