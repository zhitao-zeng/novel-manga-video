"""planning.budget responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
import math
from .issues import PlanningIssue, PlanningCode
from . import text as pc_text
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



def validate_duration(normalized, ctx, errors, warnings):
    clip_seconds: dict[str, float] = {}
    for shot in normalized:
        clip_seconds[shot["clip_hint"]] = round(clip_seconds.get(shot["clip_hint"], 0.0) + pc_text.stage_seconds(shot["turns"], ctx=ctx), 2)
    for clip_id, seconds in clip_seconds.items():
        if seconds > ctx.max_clip_seconds + pc_constants.CLIP_SECONDS_TOLERANCE:
            # The packer cuts overlong clips to this lane's duration limit.
            warnings.append(
                f"{clip_id}: 估算 {seconds} 秒超过单段上限 {int(ctx.max_clip_seconds)} 秒，打包时会自动拆成两段（report only）"
            )
    total_seconds = round(sum(clip_seconds.values()), 2)
    if ctx.episode_seconds_min and 0 < ctx.episode_seconds_min - total_seconds <= pc_constants.EPISODE_FLOOR_TOLERANCE:
        warnings.append(f"report only: 全集估算 {total_seconds} 秒，比下限 {ctx.episode_seconds_min:g} 秒少 "
                        f"{ctx.episode_seconds_min - total_seconds:g} 秒，在 {pc_constants.EPISODE_FLOOR_TOLERANCE:g} 秒估时容差内，不重写")
    elif ctx.episode_seconds_min and total_seconds < ctx.episode_seconds_min:
        errors.append(PlanningIssue(PlanningCode.DURATION_BELOW_MINIMUM, f"全集估算只有 {total_seconds} 秒，低于本次要求的下限 {ctx.episode_seconds_min:g} 秒（目标约{ctx.episode_seconds_target:g}秒）；"
            "把当前章还没拍到的事件补成阶段，把叙述里的来历、规则和动机多外化成角色对白或画外议论，"
            "或给已有阶段增加有原文依据的问答，不得注水重复同一句意思"))
    if total_seconds > ctx.episode_seconds_max and ctx.fast_tier:
        warnings.append(f"report only: 全集估算 {total_seconds} 秒，快速档不返修，打包时按 {ctx.max_clip_seconds:g} 秒拆段")
    elif total_seconds > ctx.episode_seconds_max:
        stage_total = len(normalized)
        spoken_total = sum(pc_text.spoken_chars(t["text"]) for s in normalized for t in s["turns"] if t["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"})
        scale = ctx.episode_seconds_target / total_seconds
        stage_target = max(10, round(stage_total * scale))
        errors.append(PlanningIssue(PlanningCode.DURATION_ABOVE_MAXIMUM, f"全集估算 {total_seconds} 秒，超过上限 {ctx.episode_seconds_max:g} 秒（目标约{ctx.episode_seconds_target:g}秒）。"
            f"上一稿是 {stage_total} 个阶段、发声 {spoken_total} 字；本次压到 {stage_target} 个阶段左右、"
            f"发声 {max(150, round(spoken_total * scale))} 字左右。做法是缩短台词：合并同一人的连续短句，删掉不带新信息的群众议论和感叹，"
            "去掉只有反应没有事件的无声阶段；每个阶段最多两句短台词。不得为了缩短而删掉整个区段：每个区段仍须至少被一个阶段引用，一个都不能少。不得原样重发上一稿。"))
