#!/usr/bin/env python3
from __future__ import annotations
from novel_manga.media import generation

import copy
import threading


from novel_manga.story.dialogue import rewritten_dialogue
from novel_manga.media.common import log
from novel_manga.media.policy import COMPLIANCE_SUFFIX
from novel_manga.util import atomic_write_json
import novel_manga.application.rendering.moderation as moderation_repair


POLICY = "thin-media-v22-coverage-gate"
CHAT_CONTEXT_MESSAGES = 2   # earlier messages shown above the new ones on a chat card
CHAT_HISTORY_EPISODES = 3   # how far back to look for them


# ---- subtitle helpers (v16) ----


MAX_CER = 0.5          # kept in the report; no longer gates


# An English prompt (local H3) gets its retake note in English: H3 speaks what is written in Chinese, and
# read the note above out as dialogue - 雾月 58, 59, 97, 1262 and 1736 kept such takes.




PLAN_WRITE_LOCK = threading.Lock()
FEEDBACK_FILE = "review_feedback.json"


def prescreen_prompt(prompt: str) -> float:
    """Ask the local Qwen whether the prompt is likely to trip the video service's
    content filter (violence, gore, sexual content, gambling, drugs, politics)."""
    try:
        from novel_manga.llm.client import ask_json, obj
        verdict = ask_json([{"type": "text", "text": (
            "下面是一段给视频生成模型的提示词。判断它被平台内容审核拒绝的可能性（暴力打斗细节、血腥伤势、色情或挑逗台词、赌博、毒品、自残、敏感政治），"
            "risk 为 0 到 1，reason 一句话。只输出JSON。\n\n" + prompt[:6000])}], obj({"risk": {"type": "number"}, "reason": {"type": "string"}}), name="prescreen", max_tokens=120)
        return float(verdict.get("risk", 0.0))
    except Exception as error:  # noqa: BLE001 - the prescreen is advisory
        log(f"prescreen skipped ({type(error).__name__})")
        return 0.0


def save_clip_plan(ctx) -> None:
    """Write the plan back without the runner's own bookkeeping keys."""
    with PLAN_WRITE_LOCK:
        plan = {**ctx.clip_plan, "clips": [{k: v for k, v in clip.items() if not k.startswith("_")} for clip in ctx.clip_plan["clips"]]}
        atomic_write_json(ctx.episode_dir / "clip_plan.json", plan)


def repair_refused_prompt(ctx, clip: dict, attempt: int) -> bool:
    """Rewrite a prompt the text filter refuses, verified before it is paid for."""
    def assemble(base: str) -> str:
        saved = clip["prompt"]
        clip["prompt"] = base
        try:
            return clip_prompt(ctx, clip) + retry_suffix(ctx, clip, attempt) + (COMPLIANCE_SUFFIX if ctx.state.clip(clip['clip_id']).compliance else "")
        finally:
            clip["prompt"] = saved

    # A guest character may be renamed to get past the filter (the card binding
    # is by image index, so the picture does not change), but the series leads
    # may not: their names run through every episode's plan and review.
    leads = tuple(c.name for c in ctx.bible.characters if str(getattr(c, "role", "")) in {"主角", "女主角", "男主角"})
    repaired = moderation_repair.repair(
        clip["prompt"], ctx.settings, assemble=assemble, protect=leads,
        log=lambda message: log(f"{clip['clip_id']}: {message}"))
    if not repaired:
        return False
    text, line_edits = repaired
    clip.setdefault("prompt_before_repair", clip["prompt"])
    clip["prompt"] = text
    # Subtitles, speech evaluation and H3 bindings must describe the same accepted words.
    clip.update(rewritten_dialogue(clip, line_edits))
    if line_edits:
        log(f"{clip['clip_id']}: repaired lines {[e['new'] for e in line_edits]}")
    # The plan is the cache key of a clip: keeping the accepted wording there
    # means a later run reuses this video instead of paying for it again.
    save_clip_plan(ctx, )
    return True


def retry_suffix(ctx, clip: dict, attempt: int) -> str:
    return generation.retry_suffix(ctx, clip, attempt)


def english_correction_for_new_take(ctx, clip: dict) -> bool:
    """Keep old passed caches; a new corrected H3 take must use its English version."""
    note = str(ctx.feedback.get(clip['clip_id']) or '').strip()
    if not generation.h3_lane(ctx) or not note:
        return False
    from novel_manga.application.profiles import h3_prompt_outdated
    if h3_prompt_outdated(clip, note):
        raise RuntimeError('H3 correction needs a current English translation before generating a new take')
    if generation.uses_h3_prompt(ctx, clip):
        return False
    clip.pop('prompt_h3_skip', None)
    save_clip_plan(ctx, )
    return True


def correct_speech_request(ctx, clip: dict, analysis: dict) -> bool:
    """One corrected request within the existing take limit, never a blind seed retry."""
    from novel_manga.media.speech_repair import correction, merge_note
    from novel_manga.application.rendering.h3 import convert
    from novel_manga.application.profiles import h3_prompt_outdated
    from novel_manga.story.h3 import request_issues
    old_note = str(ctx.feedback.get(clip['clip_id']) or '')
    note = merge_note(old_note, correction(clip, analysis))
    if not note or note == old_note:
        return False
    candidate = copy.deepcopy(clip)
    convert(candidate, note=note)
    if (h3_prompt_outdated(candidate, note, strict=True) or request_issues(candidate)
            or candidate.get('prompt_h3') == clip.get('prompt_h3')):
        return False
    with PLAN_WRITE_LOCK:
        before_plan = copy.deepcopy(ctx.clip_plan)
        before_notes = dict(ctx.feedback)
        clip.update(candidate)
        ctx.feedback[clip['clip_id']] = note
        atomic_write_json(ctx.episode_dir / FEEDBACK_FILE, ctx.feedback)
        from novel_manga.application.repair.history import refresh_prepared_plan
        refresh_prepared_plan(ctx.episode_dir, before_plan, ctx.clip_plan, before_notes, after_notes=ctx.feedback)
    save_clip_plan(ctx, )
    log(f"{clip['clip_id']}: extra speech; corrected the English request before the next take")
    return True


def prescreens(ctx, clip: dict) -> bool:
    """Whether the clip's wording is scored for content-filter risk, and softened, before its first submission.
    Not an English (H3) prompt: the local model has no filter to get past, and the softening - Chinese
    substitutions and a Chinese compliance paragraph - rewrote its <d> lines and was read out as dialogue (雾月,
    2026-09-11/12: 329 clips in finals carried it, 30 of them audibly)."""
    return (ctx.options.prescreen and not ctx.options.cache_only and not generation.uses_h3_prompt(ctx, clip)
            and not ctx.state.clip(clip['clip_id']).softened and not ctx.state.clip(clip['clip_id']).prescreened)


def clip_prompt(ctx, clip: dict) -> str:
    return generation.clip_prompt(ctx, clip)
