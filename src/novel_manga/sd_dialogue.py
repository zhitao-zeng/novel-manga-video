from __future__ import annotations

import re

from .models import CameraBeat, CameraPlan, MotionBeat, PerformancePlan, SceneAudioPlan


PUNCTUATION = "，。！？；：、…,.!?;:"

_LEGACY_CAMERA_DIRECTIVES = (
    "固定镜头",
    "镜头推进",
    "镜头缓慢推进",
    "镜头推近",
    "镜头缓慢推近",
    "镜头拉远",
    "镜头横移",
    "镜头环绕",
    "镜头摇摄",
    "摄影机",
    "镜头",
    "运镜",
    "推镜",
    "推近",
    "拉远",
    "横移",
    "环绕",
    "摇镜",
    "摇摄",
    "升降镜头",
    "digital zoom",
    "dolly",
    "camera move",
    "camera pan",
    "camera orbit",
)


def performance_action_only(value: str) -> str:
    """Remove legacy camera directions from the actor-performance channel."""

    clauses = [
        clause.strip()
        for clause in re.split(r"[，。；;]+", value)
        if clause.strip()
    ]
    actor_clauses = [
        clause
        for clause in clauses
        if not any(
            cue in clause.casefold() for cue in _LEGACY_CAMERA_DIRECTIVES
        )
    ]
    return "，".join(actor_clauses) or "人物完成与当前剧情直接相关的视线、手势和身体重心变化"


def _fallback_performance_plan(
    role: str,
    text: str,
    action: str,
    emotion: str | None,
) -> PerformancePlan:
    action = performance_action_only(action)
    if role == "narrator":
        return PerformancePlan(
            objective=f"通过连续动作表现“{text}”，不是静态摆拍",
            start_state="人物处于动作即将发生的准备姿态，身体重心尚未完全稳定",
            motion_beats=[
                MotionBeat(
                    phase="opening",
                    trigger="叙事事件开始",
                    action="视线先移动，头部随后转向动作目标，肩膀和上身稍后跟随",
                    reaction="身体重心随观察方向发生可见变化",
                ),
                MotionBeat(
                    phase="development",
                    trigger="人物确认当前事件",
                    action=action or "人物完成一个与剧情直接相关的明确动作",
                    reaction="手部、肩膀和身体重心依次响应，不同时启动",
                ),
                MotionBeat(
                    phase="resolution",
                    trigger="主要动作完成",
                    action="人物以一个清楚的收束动作停下，为下一镜留下衔接",
                    reaction="头发和衣摆稍晚停止，呼吸仍自然持续",
                ),
            ],
            end_state="主要动作完成，人物停在能承接下一镜的位置",
        )
    target_emotion = emotion or "符合台词的情绪"
    return PerformancePlan(
        objective=f"让{role}在说话过程中完成有因果的表演，并自然过渡到{target_emotion}",
        start_state="说话前先以自然闭嘴的准备姿态停留，目光尚未完全落到对方身上",
        motion_beats=[
            MotionBeat(
                phase="opening",
                trigger="准备开口",
                action="眼睛先看向对方，头部随后小幅转动，肩膀和上身稍后跟随并吸气",
                reaction="身体重心从后脚移向前脚",
                expression_transition=f"从克制过渡到{target_emotion}",
            ),
            MotionBeat(
                phase="development",
                trigger="说到核心信息",
                action=action or "一只手完成与当前剧情直接相关的明确动作",
                reaction="另一只手和上身自然补偿动作，视线持续对准对话对象",
                expression_transition=f"{target_emotion}逐渐清晰",
            ),
            MotionBeat(
                phase="resolution",
                trigger="台词接近结束",
                action="动作减速，抬眼确认对方反应，说完后自然闭嘴",
                reaction="肩膀放松，呼吸和衣物惯性稍后停下",
                expression_transition=f"停在{target_emotion}的收束表情",
            ),
        ],
        end_state="完成台词并闭嘴，脸部仍清楚可见，姿势能够衔接下一镜",
    )


def _fallback_camera_plan(role: str, composition: str) -> CameraPlan:
    return CameraPlan(
        mode="locked",
        motivation="未提供明确的人物位移、空间揭示或情绪转折，默认固定机位",
        action_axis="沿首次建立的行动轴同侧取景",
        screen_direction="保持人物左右位置、视线和运动方向连续",
        start_position=composition,
        camera_beats=[
            CameraBeat(
                phase="opening",
                trajectory="锁定机位，摄影机在整个表演过程中保持静止",
                framing="通过人物视线、手势、姿态和画内走位维持动态",
                parallax="不制造摄影机视差，前景、人物和背景空间关系保持固定",
            ),
            CameraBeat(
                phase="resolution",
                trajectory="继续锁定机位，让动作结果和表情停留一拍",
                framing="不推拉、不横移、不环绕，保持原有构图",
                parallax="环境锚点、空间轴线和人物屏幕位置稳定不变",
            ),
        ],
        end_position="与起始位置相同的稳定机位",
    )


def _normalize_camera_plan(camera_plan: CameraPlan) -> CameraPlan:
    """Make the mode authoritative for legacy plans with moving beat text."""

    if camera_plan.mode != "locked":
        return camera_plan
    return CameraPlan(
        mode="locked",
        motivation=camera_plan.motivation,
        action_axis=camera_plan.action_axis,
        screen_direction=camera_plan.screen_direction,
        start_position=camera_plan.start_position,
        camera_beats=[
            CameraBeat(
                phase="opening",
                trajectory="锁定机位，摄影机在整个表演过程中保持静止",
                framing="通过人物视线、手势、姿态和画内走位维持动态",
                parallax="不制造摄影机视差，前景、人物和背景空间关系保持固定",
            ),
            CameraBeat(
                phase="resolution",
                trajectory="继续锁定机位，让动作结果和表情停留一拍",
                framing="不推拉、不横移、不环绕，保持原有构图",
                parallax="环境锚点、空间轴线和人物屏幕位置稳定不变",
            ),
        ],
        end_position="与起始位置相同的稳定机位",
    )


def _timed_beats(duration: float, count: int) -> list[tuple[float, float]]:
    usable = max(4.0, min(14.0, duration))
    return [
        (usable * index / count, usable * (index + 1) / count)
        for index in range(count)
    ]


def compile_performance_prompt(
    performance_plan: PerformancePlan,
    *,
    duration: float,
) -> str:
    performance_rows = []
    for beat, (start, end) in zip(
        performance_plan.motion_beats,
        _timed_beats(duration, len(performance_plan.motion_beats)),
        strict=True,
    ):
        parts = [f"{start:.1f}-{end:.1f}秒"]
        if beat.trigger:
            parts.append(f"触发：{beat.trigger}")
        parts.append(f"动作：{beat.action}")
        if beat.reaction:
            parts.append(f"反应：{beat.reaction}")
        if beat.expression_transition:
            parts.append(f"表情：{beat.expression_transition}")
        performance_rows.append("，".join(parts))
    return (
        f"【镜头目的】{performance_plan.objective}。"
        f"【动作起点】{performance_plan.start_state}。"
        f"【动作链】{'；'.join(performance_rows)}。"
        f"【动作终点】{performance_plan.end_state}。"
    )


def compile_camera_prompt(
    camera_plan: CameraPlan,
    *,
    duration: float,
) -> str:
    camera_plan = _normalize_camera_plan(camera_plan)
    camera_rows = []
    for beat, (start, end) in zip(
        camera_plan.camera_beats,
        _timed_beats(duration, len(camera_plan.camera_beats)),
        strict=True,
    ):
        camera_rows.append(
            f"{start:.1f}-{end:.1f}秒：轨迹={beat.trajectory}；构图={beat.framing}；"
            f"空间视差={beat.parallax}"
        )
    return (
        f"【摄影机模式】{camera_plan.mode}；动机={camera_plan.motivation}。"
        f"【行动轴】{camera_plan.action_axis}；屏幕方向={camera_plan.screen_direction}。"
        f"【摄影机起点】{camera_plan.start_position}。"
        f"【摄影机轨迹】{'；'.join(camera_rows)}。"
        f"【摄影机终点】{camera_plan.end_position}。"
    )


def compile_directing_prompt(
    performance_plan: PerformancePlan,
    camera_plan: CameraPlan,
    *,
    duration: float,
) -> str:
    return compile_performance_prompt(
        performance_plan,
        duration=duration,
    ) + compile_camera_prompt(camera_plan, duration=duration)


def build_sd_prompt(
    role: str,
    text: str,
    motion_prompt: str,
    *,
    use_reference_audio: bool = False,
    actor_description: str | None = None,
    composition_prompt: str | None = None,
    emotion: str | None = None,
    performance_plan: PerformancePlan | None = None,
    camera_plan: CameraPlan | None = None,
    audio_plan: SceneAudioPlan | None = None,
    duration: float = 6.0,
) -> str:
    if use_reference_audio and role == "narrator":
        audio_instruction = (
            "严格以参考音频1作为唯一画外旁白、音色、语速、停顿和情绪依据，完整复现参考音频，"
            "不得改词、漏词、重读或添加其他人声；画中人物不得开口或随旁白做口型。"
        )
    elif use_reference_audio:
        audio_instruction = (
            "严格以参考音频1作为唯一对白、音色、语速、停顿和情绪依据，完整复现参考音频，"
            "不得改词、漏词、重读或添加其他对白；口型必须与参考音频逐字同步。"
        )
    else:
        audio_instruction = "不生成声音。"
    sound_direction = ""
    if audio_plan is not None:
        non_speech = "；".join(
            item
            for item in (
                f"环境底：{audio_plan.ambience}" if audio_plan.ambience else "",
                f"音乐提示：{audio_plan.music_cue}" if audio_plan.music_cue else "",
                (
                    f"同步音效：{'、'.join(audio_plan.sfx_events)}"
                    if audio_plan.sfx_events
                    else ""
                ),
            )
            if item
        )
        if non_speech:
            sound_direction = (
                f"【非语言声音设计】{non_speech}。这些声音不得遮住、替换或重复参考人声；"
                + ("对白出现时背景自动压低。" if audio_plan.ducking else "背景保持克制。")
            )
    composition = composition_prompt or "固定单人正脸或四分之三近景"
    performance_plan = performance_plan or _fallback_performance_plan(
        role, text, motion_prompt, emotion
    )
    camera_plan = _normalize_camera_plan(
        camera_plan or _fallback_camera_plan(role, composition)
    )
    directing = compile_directing_prompt(
        performance_plan, camera_plan, duration=duration
    )
    camera_is_locked = camera_plan.mode == "locked"
    camera_freedom = (
        "摄影机严格保持锁定机位，人物可以改变姿态和画内位置，但不得改变行动轴、左右关系或原始机位。"
        if camera_is_locked
        else (
            "允许摄影机只按计划执行一次有叙事动机的短轨迹，"
            "但不得越过行动轴、交换人物左右位置或追加第二种运镜。"
        )
    )
    continuity = (
        "参考图只用于保持国漫画风、人物身份、脸型、发型、服装、场景美术、色彩与光影。"
        "不要锁定参考图中的静态姿势；允许人物明显改变肢体姿态和身体朝向。"
        f"{camera_freedom}"
        "不得变脸，不得新增人物，不出现文字、字幕、气泡、水印或标识。"
        f"{audio_instruction}"
        f"{sound_direction}"
    )
    camera_direction = (
        "The camera is locked-off and remains entirely motionless for the whole shot. "
        "All visible motion comes from purposeful character performance and subtle environmental motion. "
        "Keep the action axis, screen direction, framing and background anchors stable. "
        if camera_is_locked
        else (
            "The camera performs only the single motivated trajectory specified above, then stops. "
            "Keep that move on one side of the action axis with visible but restrained parallax. "
            "Do not add orbiting, a second move, digital zoom or continuous drifting. "
        )
    )
    anti_static = (
        "Motion direction: This is a performed scene, not an animated still image. "
        "Use action-reaction-action progression and clear motion beats. "
        "Eyes move before the head, the head before the shoulders, and hair and clothing react slightly later. "
        f"{camera_direction}"
        "Vary character movement speed with pauses, acceleration and deceleration. "
        "Avoid holding the initial pose, mouth-only motion, mechanical turns and uniform zoom."
    )
    anti_zoom = "不要用数字推近、裁切缩放或持续漂移伪装摄影机运动。"
    if role == "narrator":
        return (
            f"{continuity}这是旁白画面，画面内所有人物都不说话，嘴巴自然闭合。"
            f"当前叙事内容是：{text}。{directing}"
            "只在叙事触发点发生一个清楚的主要动作，并给结果或人物反应留出短暂停顿；"
            "不得用无意义小动作填满时长，不切镜、不循环、不倒放，不要所有动作同时发生。"
            f"{anti_zoom}{anti_static}"
        )
    actor = actor_description or role
    performance = f"表演情绪为{emotion}。" if emotion else ""
    if use_reference_audio:
        timing = (
            "参考音频开头静音期间嘴巴保持自然闭合，只在参考音频实际人声开始时开口；"
            "嘴唇随台词逐字自然连续开合，下颌动作克制，停顿与标点自然；"
            "参考音频人声结束后立即闭嘴，并在剩余静音和镜头尾部持续闭合至少半秒。"
        )
    else:
        timing = (
            "从镜头开始后立即说话，嘴唇随台词逐字自然连续开合，下颌动作克制，"
            "停顿与标点自然，说完后自然闭嘴。"
        )
    return (
        f"{continuity}镜头明确聚焦{actor}，初始构图参考：{composition}。"
        "人物必须随表演改变姿态、视线、手势和身体重心；摄影机构图只按既定模式改变。"
        "只有这个角色开口，其他人物保持闭嘴，严格保证只有该角色开口。"
        f"{performance}"
        f"该角色正在用标准普通话、符合当前情绪地自然说出完整台词：“{text}”"
        f"{timing}禁止嘴部静止、夸张大张嘴、嘴形抖动、五官扭曲或其他人物同时说话。"
        "说话人的脸和嘴在整个镜头中始终清晰可见，可以移动和改变姿势，但不能转身背对镜头、离开画面或被遮挡。"
        f"{directing}全程为同一个连续镜头，不切镜、不转场；动作和摄影机运动互相配合，"
        "动作变化必须由台词含义、视线目标、道具状态或对方反应触发；"
        "一个节拍只做一个主要动作，允许克制停顿，摄影机关系只按既定模式执行，不要随机抽动。"
        f"{anti_zoom}{anti_static}"
    )


def _hard_wrap(text: str, limit: int) -> list[str]:
    chunks = [text[index:index + limit] for index in range(0, len(text), limit)] or [""]
    if len(chunks) >= 2 and chunks[-1] and all(char in PUNCTUATION for char in chunks[-1]):
        chunks[-2] += chunks[-1]
        chunks.pop()
    return chunks


def subtitle_pages(text: str, chars_per_line: int = 18) -> list[str]:
    """Wrap subtitles into at most two lines without punctuation-only pages."""
    clean = "".join(text.split())
    clauses = re.findall(rf".+?[{re.escape(PUNCTUATION)}]+|.+$", clean) or [clean]
    lines: list[str] = []
    current = ""
    for clause in clauses:
        if current and len(current) + len(clause) <= chars_per_line:
            current += clause
            continue
        if current:
            lines.append(current)
            current = ""
        if len(clause) <= chars_per_line:
            current = clause
        else:
            wrapped = _hard_wrap(clause, chars_per_line)
            lines.extend(wrapped[:-1])
            current = wrapped[-1]
    if current or not lines:
        lines.append(current)

    pages = [r"\N".join(lines[index:index + 2]) for index in range(0, len(lines), 2)]
    if len(pages) >= 2 and all(char in PUNCTUATION + r"\N" for char in pages[-1]):
        pages[-2] += pages.pop().replace(r"\N", "")
    return pages


def timed_subtitle_pages(text: str, start: float, end: float) -> list[dict[str, float | str]]:
    pages = subtitle_pages(text)
    duration = max(0.01, end - start)
    weights = [max(1, sum(char not in PUNCTUATION + r"\N" for char in page)) for page in pages]
    total_weight = sum(weights)
    cursor = start
    events: list[dict[str, float | str]] = []
    for index, (page, weight) in enumerate(zip(pages, weights, strict=True)):
        page_end = end if index == len(pages) - 1 else cursor + duration * weight / total_weight
        events.append({"start": cursor, "end": page_end, "text": page})
        cursor = page_end
    return events
