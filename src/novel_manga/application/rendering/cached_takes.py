#!/usr/bin/env python3
from __future__ import annotations

import json


from novel_manga.media import cache


POLICY = "thin-media-v22-coverage-gate"
CHAT_CONTEXT_MESSAGES = 2   # earlier messages shown above the new ones on a chat card
CHAT_HISTORY_EPISODES = 3   # how far back to look for them


# ---- subtitle helpers (v16) ----


MAX_CER = 0.5          # kept in the report; no longer gates


# An English prompt (local H3) gets its retake note in English: H3 speaks what is written in Chinese, and
# read the note above out as dialogue - 雾月 58, 59, 97, 1262 and 1736 kept such takes.



from . import clip_analysis


def approved_cached_take(ctx, clip: dict) -> dict | None:
    """Reuse proof from an existing take, never issue a new over-budget request."""
    from novel_manga.application.profiles import h3_prompt_outdated
    if ctx.settings.local_h3_base_url and h3_prompt_outdated(clip, str(ctx.feedback.get(clip["clip_id"], ""))):
        return None
    for path in sorted((ctx.work / "clips" / clip["clip_id"]).glob("attempt_*/asr.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            attempt = int(path.parent.name.split("_")[-1])
            if cache.cached_take(ctx, clip, attempt):
                record = clip_analysis.recheck_speech(ctx, clip, record, path.with_name('clip.mp4'))
                record = clip_analysis.check_clip_black(ctx, clip, record, path.with_name("clip.mp4"))
                if record["passed"]:
                    return {**record, "clip_id": clip["clip_id"], "video": str(path.with_name("clip.mp4"))}
        except (OSError, ValueError):
            continue
    return None
