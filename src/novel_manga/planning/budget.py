"""planning.budget responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
import math
from .issues import PlanningIssue, PlanningCode
from . import text as pc_text
import novel_manga.planning.constants as pc_constants
from novel_manga.story.compilation import line_parts

def configure_budget(text_count: int, *, shape: str, min_seconds: float = 0.0, ctx: PlannerContext) -> dict:
    """Fixed episodes retain their target; flex episodes follow the screenplay.

    Explicit minimum/maximum seconds remain caller constraints. No default
    episode duration or word count is inferred from chapter character count.
    """
    ctx.episode_shape = shape
    ctx.max_clip_seconds = ctx.clip_seconds_max
    if shape == 'flex':
        # Zero means no episode target/ceiling, as it already does for the floor.
        # Clip duration remains an execution limit; source length is not a clock.
        ctx.episode_seconds_target = ctx.episode_seconds_max = 0.0
        ctx.episode_seconds_min = min_seconds
        ctx.clip_range = ctx.spoken_range = (0, 0)
        ctx.stage_range = (1, 6)
        return {'clip_seconds_max': ctx.max_clip_seconds,
                'episode_min_seconds': min_seconds,
                'episode_floor_tolerance_seconds': pc_constants.EPISODE_FLOOR_TOLERANCE}
    target = 90
    ctx.episode_seconds_target = max(float(target), min_seconds)
    base_range = (6, 8) if ctx.short_clips else (3, 4)
    needed = math.ceil(ctx.episode_seconds_target / ctx.max_clip_seconds)
    if needed > 10:
        raise ValueError(f"目标 {ctx.episode_seconds_target:g} 秒超出最多 10 段 × {ctx.max_clip_seconds:g} 秒的规划容量")
    ctx.clip_range = (base_range[0], max(base_range[1], needed))
    ctx.stage_range = (2, 3) if ctx.short_clips else (4, 6)
    ctx.spoken_range = (220, 300)
    ctx.episode_seconds_min = min_seconds
    # The target is what the planner aims at; the ceiling is what it may not cross.  They were 15
    # seconds apart, so a dense chapter hit the wall on its first draft and was sent back to cut
    # something - 24 of this book's 100 episodes came out between 95 and 105 seconds, squeezed,
    # while 38 finished under 80 because their chapters are short and the ceiling never mattered.
    # 120 is not a loosening so much as the number the structure already allows: both lanes reach
    # it (4 clips x 30 s and 8 x 15 s), and the 105 was written over the top of that.
    ctx.episode_seconds_max = min(120.0, ctx.clip_range[1] * ctx.max_clip_seconds)
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
    if ctx.episode_shape == 'flex':
        return {'clip_seconds': f'最多{ctx.max_clip_seconds:g}秒',
                'episode_target': '整集时长按剧情需要；保留关键因果、决定与必要对白，删重复表达，不凑时长。',
                **({'episode_min_seconds': ctx.episode_seconds_min} if ctx.episode_seconds_min else {}),
                **({'episode_max_seconds': ctx.episode_seconds_max} if ctx.episode_seconds_max else {})}
    return {
        "clip_count": f"{ctx.clip_range[0]}-{ctx.clip_range[1]}",
        "stages_per_clip": f"{ctx.stage_range[0]}-{ctx.stage_range[1]}",
        "clip_seconds": f"{10 if ctx.short_clips else 20}-{ctx.max_clip_seconds:g}",
        "episode_seconds": f"about {ctx.episode_seconds_target:g}, max {ctx.episode_seconds_max:g}",
        "episode_target": f"约{ctx.episode_seconds_target:g}秒；尽量不低于{ctx.episode_seconds_min:g}秒；"
                          "允许合理改编取舍，不为凑时长新增或重复剧情",
        "spoken_chars_total": f"{ctx.spoken_range[0]}-{ctx.spoken_range[1]}",
        "episode_seconds_min": ctx.episode_seconds_min,
        "episode_floor_tolerance_seconds": pc_constants.EPISODE_FLOOR_TOLERANCE,
    }


def directed_duration_problem(seconds: float, maximum: float | None) -> str | None:
    if maximum is not None and seconds > maximum:
        return (f'全片计划{seconds:g}秒，超出明确指定的{maximum:g}秒上限。'
                '保留主线与结尾，调整实际镜长或删除重复表达；不得截断台词、删整场或把跨时空事件塞进一镜。')
    return None



def validate_duration(normalized, ctx, errors, warnings):
    clip_seconds: dict[str, float] = {}
    for shot in normalized:
        # same rule as ClipCompiler.shot_seconds: a planned length is a planned length
        seconds = float(shot['duration_seconds']) if 'duration_seconds' in shot else pc_text.stage_seconds(shot["turns"], ctx=ctx)
        if seconds > ctx.max_clip_seconds:
            # A stage whose lines can be dealt into clips that fit is the packer's to cut - between its
            # lines, with nothing written for the new parts (story.compilation.line_parts, shared so that
            # what passes here is exactly what the packer cuts).  Handed to the writer, the cut failed ch12
            # part one in all 17 drafts of three runs (2026-09-25): the 90-character speech stayed whole,
            # or came back rewritten or repeated, or another beat fell out.  Only a length no line can
            # cut goes back to the writer: a planned duration, or a silent action longer than a clip.
            parts = [] if 'duration_seconds' in shot else line_parts(
                shot["turns"], max_seconds=ctx.max_clip_seconds, seconds_of=lambda turns: pc_text.stage_seconds(turns, ctx=ctx))
            if len(parts) > 1 and all(pc_text.stage_seconds(part, ctx=ctx) <= ctx.max_clip_seconds for part in parts):
                warnings.append(f"{shot.get('label')}: 本镜估算{seconds:g}秒，打包时在台词之间切成{len(parts)}段，"
                                "不改台词、不补写动作（report only）")
            else:
                errors.append(PlanningIssue(PlanningCode.STAGE_ABOVE_MAXIMUM,
                    f'本镜估算{seconds:g}秒，超过单镜上限{ctx.max_clip_seconds:g}秒。'
                    '由编剧拆成多个时长合规的完整阶段，逐镜写清起点、事件和末态；'
                    '保留全部必要对白和剧情，不重复动作，也不能只缩短duration_seconds掩盖超长对白。',
                    stage=shot.get('label'), field='turns'))
        clip_seconds[shot["clip_hint"]] = round(clip_seconds.get(shot["clip_hint"], 0.0) + seconds, 2)
    for clip_id, seconds in clip_seconds.items():
        if seconds > ctx.max_clip_seconds + pc_constants.CLIP_SECONDS_TOLERANCE:
            # The packer cuts overlong clips to this lane's duration limit.
            warnings.append(
                f"{clip_id}: 估算 {seconds} 秒超过单段上限 {int(ctx.max_clip_seconds)} 秒，打包按已有阶段边界分段（report only）"
            )
    total_seconds = round(sum(clip_seconds.values()), 2)
    if ctx.episode_seconds_min and 0 < ctx.episode_seconds_min - total_seconds <= pc_constants.EPISODE_FLOOR_TOLERANCE:
        warnings.append(f"report only: 全集估算 {total_seconds} 秒，比下限 {ctx.episode_seconds_min:g} 秒少 "
                        f"{ctx.episode_seconds_min - total_seconds:g} 秒，在 {pc_constants.EPISODE_FLOOR_TOLERANCE:g} 秒估时容差内，不重写")
    elif ctx.episode_seconds_min and total_seconds < ctx.episode_seconds_min:
        errors.append(PlanningIssue(PlanningCode.DURATION_BELOW_MINIMUM, f"全集估算只有 {total_seconds} 秒，低于本次要求的下限 {ctx.episode_seconds_min:g} 秒（目标约{ctx.episode_seconds_target:g}秒）；"
            "把当前章还没拍到的事件补成阶段，把叙述里的来历、规则和动机多外化成角色对白或画外议论，"
            "或给已有阶段增加有原文依据的问答，不得注水重复同一句意思"))
    if ctx.story_blueprint.get('version') == 'scene-screenplay-v2':
        problem = directed_duration_problem(total_seconds, ctx.story_blueprint.get('episode_seconds_max'))
        if problem:
            errors.append(PlanningIssue(PlanningCode.DURATION_ABOVE_MAXIMUM, problem))
        elif ctx.episode_seconds_target and total_seconds > ctx.episode_seconds_target:
            warnings.append(f'导演计划{total_seconds:g}秒，高于参考目标{ctx.episode_seconds_target:g}秒；未擅自删剧情或压缩台词。')
    elif ctx.episode_seconds_max and total_seconds > ctx.episode_seconds_max and ctx.authored_storyboard:
        warnings.append(f"report only: 全集估算 {total_seconds} 秒，高于上限 {ctx.episode_seconds_max:g} 秒；"
                        "分镜是人写的，时长按作者的镜头表走，不压缩")
    elif ctx.episode_seconds_max and total_seconds > ctx.episode_seconds_max and ctx.episode_shape == 'flex':
        errors.append(PlanningIssue(PlanningCode.DURATION_ABOVE_MAXIMUM,
            directed_duration_problem(total_seconds, ctx.episode_seconds_max)))
    elif ctx.episode_seconds_max and total_seconds > ctx.episode_seconds_max:
        stage_total = len(normalized)
        spoken_total = sum(pc_text.spoken_chars(t["text"]) for s in normalized for t in s["turns"] if t["delivery_mode"] in {"visible_dialogue", "offscreen_dialogue"})
        scale = ctx.episode_seconds_target / total_seconds
        stage_target = max(10, round(stage_total * scale))
        errors.append(PlanningIssue(PlanningCode.DURATION_ABOVE_MAXIMUM, f"全集估算 {total_seconds} 秒，超过上限 {ctx.episode_seconds_max:g} 秒（目标约{ctx.episode_seconds_target:g}秒）。"
            f"上一稿是 {stage_total} 个阶段、发声 {spoken_total} 字；本次压到 {stage_target} 个阶段左右、"
            f"发声 {max(150, round(spoken_total * scale))} 字左右。做法是缩短台词：合并同一人的连续短句，删掉不带新信息的群众议论和感叹，"
            "去掉只有反应没有事件的无声阶段；每个阶段最多两句短台词。可以合理合并和省略，但不能删掉理解当前行动所必需的事实。不得原样重发上一稿。"))
