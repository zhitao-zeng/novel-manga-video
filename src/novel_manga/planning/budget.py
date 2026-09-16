"""planning.budget responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
import math
import novel_manga.planning.constants as pc_constants

def configure_budget(text_count: int, *, fast: bool, min_seconds: float = 0.0, ctx: PlannerContext) -> dict:
    """Use one episode budget for the model brief, request and validation.

    A long 15-second episode needs more than eight clips. Keep the usual range
    for short chapters, and expand it only when the target cannot fit.
    """
    ctx.max_clip_seconds = ctx.clip_seconds_max
    target = min(150, max(75, round(text_count / 3000 * 85 / 10) * 10)) if fast else 90
    ctx.episode_seconds_target = max(float(target), min_seconds)
    base_range = (6, 8) if ctx.short_clips else ((3, 5) if fast else (3, 4))
    needed = math.ceil(ctx.episode_seconds_target / ctx.max_clip_seconds)
    if needed > 10:
        raise ValueError(f"目标 {ctx.episode_seconds_target:g} 秒超出最多 10 段 × {ctx.max_clip_seconds:g} 秒的规划容量")
    ctx.clip_range = (base_range[0], max(base_range[1], needed))
    ctx.stage_range = (2, 3) if ctx.short_clips else ((3, 5) if fast else (4, 6))
    ctx.spoken_range = ((180, 300) if ctx.episode_seconds_target <= 90 else (240, 400)) if fast else (220, 300)
    ctx.episode_seconds_min = max(min_seconds, ctx.episode_seconds_target - 25 if fast else 0)
    ctx.episode_seconds_max = min(210.0 if fast else 105.0, ctx.clip_range[1] * ctx.max_clip_seconds)
    if ctx.episode_seconds_target > ctx.episode_seconds_max:
        raise ValueError(f"目标 {ctx.episode_seconds_target:g} 秒超过本档规划上限 {ctx.episode_seconds_max:g} 秒")
    return {
        "episode_target_seconds": ctx.episode_seconds_target,
        "episode_min_seconds": ctx.episode_seconds_min,
        "episode_floor_tolerance_seconds": pc_constants.EPISODE_FLOOR_TOLERANCE,
        "episode_max_seconds": ctx.episode_seconds_max,
        "clip_seconds": [10 if ctx.short_clips else 20, ctx.max_clip_seconds],
        "clip_count": list(ctx.clip_range), "stages_per_clip": list(ctx.stage_range),
        "spoken_chars": list(ctx.spoken_range),
    }


def budget_requirements(*, ctx: PlannerContext) -> dict:
    return {
        "clip_count": f"{ctx.clip_range[0]}-{ctx.clip_range[1]}",
        "stages_per_clip": f"{ctx.stage_range[0]}-{ctx.stage_range[1]}",
        "clip_seconds": f"{10 if ctx.short_clips else 20}-{ctx.max_clip_seconds:g}",
        "episode_seconds": f"about {ctx.episode_seconds_target:g}, max {ctx.episode_seconds_max:g}",
        "episode_target": f"约{ctx.episode_seconds_target:g}秒；尽量不低于{ctx.episode_seconds_min:g}秒；"
                          f"{pc_constants.SEGMENT_COUNT}个原文区段都必须引用，skipped_segments 必须为空；不为凑时长新增或重复剧情",
        "spoken_chars_total": f"{ctx.spoken_range[0]}-{ctx.spoken_range[1]}",
        "episode_seconds_min": ctx.episode_seconds_min,
        "episode_floor_tolerance_seconds": pc_constants.EPISODE_FLOOR_TOLERANCE,
    }
