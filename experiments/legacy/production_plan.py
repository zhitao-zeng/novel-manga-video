from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from pathlib import Path

from PIL import Image, ImageDraw

from novel_manga.media.asset_factory import SeriesAssetFactory, sha256_text, sha256_file, _archive_stale
from novel_manga.config import Settings
from novel_manga.models import (
    CameraBeat,
    CameraPlan,
    Character,
    Episode,
    EpisodePlan,
    MotionActionType,
    MotionBeat,
    PerformancePlan,
    SpeechStrategy,
    ScriptTurn,
    StoryBible,
    TurnDelivery,
    TurnDerivation,
    VisualStrategy,
)
from novel_manga.production_models import (
    ActionPhysicsPlan,
    AssetRecord,
    ProductionPlan,
    RuntimeScene,
    RuntimeShot,
    RuntimeUnit,
    SceneSpatialContract,
    SeriesAssetManifest,
)
from novel_manga.providers.base import ImageResult, MediaProvider
from novel_manga.sd_dialogue import build_sd_prompt
from novel_manga.util import atomic_write_json










def _resolve_location_id(location: str, assets: SeriesAssetManifest) -> str:
    exact = next((record.asset_id for record in assets.locations if record.name == location), None)
    return exact or assets.locations[0].asset_id


def _turns_for_shot(plan_shot) -> list[ScriptTurn]:
    if plan_shot.turns:
        return plan_shot.turns
    return [
        ScriptTurn(
            text=plan_shot.subtitle,
            source_quote=plan_shot.source_quote,
        )
    ]


_DIALOGUE_COMPOSITIONS = (
    "平视正面胸像，人物居中，场景纵深位于身后并保持浅景深",
    "左前方四分之三胸像，人物位于右侧三分线，左后方保留场景标志物",
    "右前方四分之三胸像，人物位于左侧三分线，右后方保留场景标志物",
    "轻微低机位正面近景，人物略偏左，背景建筑线条向人物汇聚",
    "轻微高机位四分之三近景，人物略偏右，背景地面纹理形成纵深",
    "较紧的肩部正面近景，人物位于中央上半部，背景只保留柔化色块",
    "较宽的胸像近景，人物位于左侧，右侧留出与剧情方向一致的空间",
    "较宽的胸像近景，人物位于右侧，左侧留出与剧情方向一致的空间",
    "左侧轮廓光的正面近景，人物居中，远景光源形成稳定层次",
    "右侧轮廓光的四分之三近景，人物略偏左，背景保持克制",
    "平视侧前方近景，人物目光朝画面内侧，背后保留一处环境锚点",
    "轻微仰拍的肩部近景，人物目光稳定，场景旗帜或立柱在远处虚化",
    "轻微俯拍的胸像近景，人物位于中轴，场景台阶或地面在远处虚化",
)

_DIALOGUE_AXIS_COMPOSITIONS = (
    (
        "左前方四分之三胸像，人物位于画面右侧三分线，目光朝画面左侧内收，左后方保留场景标志物",
        "较紧的左前方四分之三肩部近景，人物仍在画面右侧三分线，目光朝画面左侧，背景只保留一处柔化锚点",
        "较宽的左前方四分之三腰上景，人物位于画面右侧三分线，目光朝画面左侧，前景保留与当前地点一致的固定空间锚点",
    ),
    (
        "右前方四分之三胸像，人物位于画面左侧三分线，目光朝画面右侧内收，右后方保留场景标志物",
        "较紧的右前方四分之三肩部近景，人物仍在画面左侧三分线，目光朝画面右侧，背景只保留一处柔化锚点",
        "较宽的右前方四分之三腰上景，人物位于画面左侧三分线，目光朝画面右侧，前景保留与当前地点一致的固定空间锚点",
    ),
)


def _dialogue_composition(
    shot_index: int,
    turn_index: int,
    speaker_name: str,
    speaker_slot: int | None = None,
) -> str:
    """Pick a repeatable side of the action axis for one visible speaker.

    The old index-only rotation could put the same speaker on alternating sides
    of the frame in one conversation.  A stable speaker hash keeps eyelines and
    screen direction continuous while retaining some episode-level variety.
    """

    if speaker_slot is not None:
        side_variants = _DIALOGUE_AXIS_COMPOSITIONS[speaker_slot % 2]
        return side_variants[(shot_index + turn_index - 2) % len(side_variants)]
    stable_index = int(hashlib.sha256(speaker_name.encode("utf-8")).hexdigest()[:8], 16)
    return _DIALOGUE_COMPOSITIONS[stable_index % len(_DIALOGUE_COMPOSITIONS)]


def _character_identity(character: Character) -> str:
    appearance = character.appearance.removeprefix("固定")
    wardrobe = character.wardrobe.removeprefix("固定")
    return (
        f"{character.name}，{character.gender}，{character.age}，"
        f"外貌锚点：{appearance}；服装锚点：{wardrobe}"
    )


def _dialogue_visual_context(shot, speaker_name: str) -> str:
    """Keep environment/blocking clauses without leaking another named actor.

    Dialogue units intentionally use a single visible-speaker reference.  The
    shot-level visual prompt can still carry useful scene anchors, but clauses
    naming another actor would fight that identity gate and reintroduce cast
    swaps.  Clause filtering preserves the current speaker's position and
    props while leaving the listener off screen.
    """
    if shot.visual_strategy == VisualStrategy.STORY_KEYFRAME:
        return shot.visual_prompt
    other_characters = [name for name in shot.characters if name != speaker_name]
    clauses = [
        clause.strip()
        for clause in re.split(r"[，,；;。]+", shot.visual_prompt)
        if clause.strip()
    ]
    safe = [
        clause
        for clause in clauses
        if not any(name in clause for name in other_characters)
    ]
    return "，".join(safe)


_OFFSCREEN_ENTITY_MARKERS = (
    "画外",
    "画面外",
    "镜外",
    "不入画",
    "不出现",
    "只作为视线对象",
)


def visible_character_names_for_shot(
    shot,
    available_names: list[str] | tuple[str, ...],
) -> list[str]:
    """Resolve visible named actors from the shot, not merely its speaker."""

    resolved = list(
        dict.fromkeys(name for name in shot.characters if name in available_names)
    )
    searchable = "；".join(
        value for value in (shot.visual_prompt, shot.motion_prompt) if value
    )
    clauses = [
        clause.strip()
        for clause in re.split(r"[，,；;。]+", searchable)
        if clause.strip()
    ]
    for name in available_names:
        mentions = [clause for clause in clauses if name in clause]
        if mentions and any(
            not any(marker in clause for marker in _OFFSCREEN_ENTITY_MARKERS)
            for clause in mentions
        ):
            resolved.append(name)
    for turn in shot.turns:
        if turn.speaking and turn.speaker_name in available_names:
            resolved.append(turn.speaker_name)
    return list(dict.fromkeys(resolved))


def _performance_plan_for(shot, turn: ScriptTurn) -> PerformancePlan:
    other_characters = (
        [name for name in shot.characters if name != turn.speaker_name]
        if turn.speaking
        else []
    )
    if shot.performance_plan is not None:
        return shot.performance_plan
    visible_action = shot.visual_prompt.split("，内容健康克制", 1)[0].rstrip("。，")
    if turn.speaking:
        if any(name in visible_action for name in other_characters):
            visible_action = (
                "说到核心信息时，一只手完成与本句道具或事件有关的明确动作，"
                "上身和身体重心随动作自然变化"
            )
        return PerformancePlan(
            objective=(
                f"让{turn.speaker_name}在说话过程中完成与剧情有关的动作，"
                f"情绪从克制自然过渡到{turn.emotion}，不是固定姿势说完整段台词"
            ),
            start_state=(
                f"{turn.speaker_name}处于动作开始前一瞬，嘴巴自然闭合，"
                "目光和身体重心尚未完全转向对话对象"
            ),
            motion_beats=[
                MotionBeat(
                    phase="opening",
                    trigger="参考音频人声即将开始",
                    action="眼睛先移向对话对象，头部随后小幅转动，肩膀和上身稍后跟随并吸气",
                    reaction="身体重心从后向前移动，手指先出现细微准备动作",
                    expression_transition=f"从克制转为{turn.emotion}",
                ),
                MotionBeat(
                    phase="development",
                    trigger="说到本句核心信息",
                    action=visible_action,
                    reaction=(
                        "主要手部动作带动肩膀与身体重心变化，视线短暂跟随道具或动作后重新看向对方"
                    ),
                    expression_transition=f"{turn.emotion}逐渐清晰但不过度夸张",
                ),
                MotionBeat(
                    phase="resolution",
                    trigger="台词接近结束",
                    action="动作减速，抬眼确认对方反应，说完后自然闭嘴并停住",
                    reaction="肩膀放松，头发和衣摆比身体稍晚停止",
                    expression_transition=f"停在{turn.emotion}的收束表情",
                ),
            ],
            end_state="台词完整结束，人物自然闭嘴，动作结果和最终表情清楚可见",
        )
    return PerformancePlan(
        objective=f"通过连续动作讲清“{turn.text}”，不是静态插画加数字推近",
        start_state="人物处于主要动作发生前一瞬，保留明确的移动方向和动作空间",
        motion_beats=[
            MotionBeat(
                phase="opening",
                trigger="叙事事件开始",
                action="视线先找到事件目标，头部随后转动，肩膀和身体重心依次跟随",
                reaction="手部开始为主要动作做准备",
            ),
            MotionBeat(
                phase="development",
                trigger="人物确认当前事件",
                action=visible_action,
                reaction="动作产生可见的身体位移、道具惯性或环境反馈",
            ),
            MotionBeat(
                phase="resolution",
                trigger="主要动作完成",
                action="人物完成一个清楚的收束反应并停在下一镜可衔接的位置",
                reaction="呼吸、头发和衣物惯性稍后停止",
            ),
        ],
        end_state="本镜事件结果清楚可读，人物嘴巴保持闭合",
    )


_SHOT_BEAT_SECONDS = {
    "reaction": 4.0,
    "transition": 4.0,
    "establish": 5.0,
    "advance": 5.0,
    "pressure": 6.0,
    "payoff": 6.0,
    "withhold": 7.0,
    "reveal": 7.0,
    "cliffhanger": 9.0,
}

def _spoken_char_count(text: str) -> int:
    """Count pronounceable text, excluding spacing and punctuation."""

    return len(re.sub(r"[\s，。！？；：、…,.!?;:\"“”'‘’（）()]", "", text))


def _motion_action_weight(action: str) -> float:
    weight = 1.0 + min(0.8, len(action) / 48.0)
    if any(token in action for token in ("走", "跑", "追", "绕", "穿过", "转身")):
        weight += 0.55
    if any(token in action for token in ("按", "握", "挡", "行礼", "回头", "推", "拉")):
        weight += 0.3
    return weight


def _infer_motion_action_type(action: str) -> MotionActionType:
    if any(token in action for token in ("拒绝", "绕过", "甩开", "不接", "不退")):
        return MotionActionType.REFUSE
    if any(token in action for token in ("质问", "发问", "追问", "？", "?")):
        return MotionActionType.ASK
    if any(token in action for token in ("直视", "挡住", "反击", "迎上", "逼近")):
        return MotionActionType.CONFRONT
    if any(token in action for token in ("走", "跑", "追", "绕", "穿过", "转身", "上前", "退后")):
        return MotionActionType.MOVE
    if any(token in action for token in ("揭开", "亮起", "显现", "出现", "展示", "照亮")):
        return MotionActionType.REVEAL
    if any(token in action for token in ("决定", "选择", "主动", "握紧")):
        return MotionActionType.CHOOSE
    if any(token in action for token in ("嘲讽", "施压", "压住", "逼问", "挑衅")):
        return MotionActionType.PRESS
    if any(token in action for token in ("等待", "停住", "保持", "站定")):
        return MotionActionType.WAIT
    return MotionActionType.REACT


def _materialize_performance_timing(shot, plan: PerformancePlan) -> PerformancePlan:
    """Fill legacy beat timing without overriding explicit direction values."""

    beats = list(plan.motion_beats)
    missing = [index for index, beat in enumerate(beats) if beat.seconds is None]
    explicit = sum(float(beat.seconds or 0.0) for beat in beats)
    allocated: dict[int, float] = {}
    if missing:
        default_total = _SHOT_BEAT_SECONDS[shot.shot_intent.dramatic_function]
        remaining = max(0.5 * len(missing), default_total - explicit)
        weights = [_motion_action_weight(beats[index].action) for index in missing]
        weight_total = sum(weights) or 1.0
        allocated = {
            index: round(remaining * weight / weight_total, 3)
            for index, weight in zip(missing, weights, strict=True)
        }
    timed = []
    for index, beat in enumerate(beats):
        update: dict[str, object] = {}
        if beat.seconds is None:
            update["seconds"] = allocated[index]
        if not beat.end_state:
            update["end_state"] = (
                beat.expression_transition or beat.reaction or f"{beat.action}完成并落定"
            )
        if "action_type" not in beat.model_fields_set:
            update["action_type"] = _infer_motion_action_type(beat.action)
        timed.append(beat.model_copy(update=update))
    return plan.model_copy(update={"motion_beats": timed})


def _assign_motion_beat_indexes(
    performance_plan: PerformancePlan,
    unit_count: int,
) -> list[list[int]]:
    """Assign each ordered beat once to a phase-appropriate runtime unit."""

    assignments = [[] for _ in range(unit_count)]
    if unit_count == 1:
        assignments[0] = list(range(len(performance_plan.motion_beats)))
        return assignments
    development = [
        index
        for index, beat in enumerate(performance_plan.motion_beats)
        if beat.phase == "development"
    ]
    development_rank = {index: rank for rank, index in enumerate(development)}
    last_unit = 0
    for index, beat in enumerate(performance_plan.motion_beats):
        if beat.phase == "opening":
            preferred = 0
        elif beat.phase == "resolution":
            preferred = unit_count - 1
        elif len(development) == 1:
            preferred = (unit_count - 1) // 2
        else:
            ratio = (development_rank[index] + 1) / (len(development) + 1)
            preferred = round(ratio * (unit_count - 1))
        selected = max(last_unit, min(unit_count - 1, preferred))
        assignments[selected].append(index)
        last_unit = selected
    return assignments


def _performance_plan_for_unit(
    plan: PerformancePlan,
    beat_indexes: list[int],
    *,
    actor: str,
) -> PerformancePlan | None:
    if not beat_indexes:
        return None
    beats = []
    for index in beat_indexes:
        beat = plan.motion_beats[index]
        beats.append(
            beat.model_copy(
                update={
                    "actor": beat.actor or actor,
                }
            )
        )
    first_index = beat_indexes[0]
    last_index = beat_indexes[-1]
    start_state = (
        plan.start_state
        if first_index == 0
        else plan.motion_beats[first_index - 1].end_state
    )
    end_state = (
        plan.end_state
        if last_index == len(plan.motion_beats) - 1
        else plan.motion_beats[last_index].end_state
    )
    return PerformancePlan(
        objective=plan.objective,
        start_state=start_state,
        motion_beats=beats,
        end_state=end_state,
    )


def _planned_unit_seconds(turn: ScriptTurn, plan: PerformancePlan | None) -> float:
    dialogue_seconds = (
        0.0
        if turn.delivery_mode
        in {TurnDelivery.TITLE_CARD, TurnDelivery.SILENT_ACTION}
        else _spoken_char_count(turn.text) / 4.0 + 1.0
    )
    beat_seconds = sum(
        float(beat.seconds or 0.0) for beat in plan.motion_beats
    ) if plan is not None else 0.0
    return round(min(14.0, max(4.0, dialogue_seconds + beat_seconds)), 3)


_CAMERA_MOVEMENT_CUES = (
    "进入", "走进", "冲入", "跑", "追", "后退", "上前", "离开",
    "揭开", "打开", "推开", "拉开", "发现", "出现", "现身", "登场", "闯入", "逼近",
)
_CAMERA_EMPHASIS_CUES = (
    "高潮", "climax", "反转", "揭晓", "真相", "冲突", "爆发", "决裂", "突袭", "危机",
)


def _camera_mode_for(shot, turn: ScriptTurn) -> tuple[str, str, int]:
    requested_plan = shot.camera_plan
    requested_mode = requested_plan.mode if requested_plan is not None else "locked"
    requested_motivation = (
        requested_plan.motivation if requested_plan is not None else ""
    )
    text = " ".join(
        filter(
            None,
            (
                shot.scene_job,
                shot.visual_prompt,
                shot.motion_prompt,
                turn.text,
                turn.emotion,
                requested_motivation,
            ),
        )
    ).casefold()
    has_emphasis = any(cue in text for cue in _CAMERA_EMPHASIS_CUES)
    has_displacement_or_reveal = any(cue in text for cue in _CAMERA_MOVEMENT_CUES)
    dramatic_function = shot.shot_intent.dramatic_function
    if requested_plan is not None:
        return (
            requested_mode,
            requested_motivation or "分镜显式摄影机计划",
            500 if requested_mode == "motivated_emphasis" else 450,
        )
    if dramatic_function in {"payoff", "cliffhanger"}:
        return (
            "motivated_emphasis",
            "镜头叙事落点需要一次克制的强调性重新构图",
            350,
        )
    if has_emphasis:
        return (
            "motivated_emphasis",
            "关键冲突、权力变化或信息揭示需要一次强调性重新构图",
            300,
        )
    if dramatic_function == "reveal":
        return (
            "motivated_subtle",
            "新信息被角色读懂时进行一次轻微的视觉收紧",
            220,
        )
    if dramatic_function == "pressure":
        return (
            "motivated_subtle",
            "人物承受压力或形成反抗时进行一次轻微的视觉收紧",
            230,
        )
    if has_displacement_or_reveal:
        return (
            "motivated_subtle",
            "人物发生明确位移或画面需要揭示新信息",
            200,
        )
    if dramatic_function == "establish":
        return (
            "motivated_subtle",
            "建立空间时使用一次极慢推进或短距离重新构图",
            180,
        )
    return (
        "locked",
        "本镜由人物动作、视线和表情承担动态，无需摄影机移动",
        0,
    )


def _camera_plan_for(
    shot,
    turn: ScriptTurn,
    composition: str,
    *,
    forced_mode: str | None = None,
    forced_motivation: str | None = None,
) -> CameraPlan:
    location = shot.location or "当前场景"
    mode, motivation, _ = _camera_mode_for(shot, turn)
    if forced_mode is not None:
        mode = forced_mode
        motivation = forced_motivation or motivation
    action_axis = f"{location}首次建立的人物视线或运动轴；摄影机始终停留在同一侧"
    visible_subject = (
        turn.speaker_name
        if turn.speaking
        else next((name for name in shot.characters if name), "当前可见主体")
    )
    screen_direction = (
        f"{visible_subject}始终保持“{composition}”所建立的画面侧和目光方向；"
        "除非画面完整展示走位，否则人物不得左右互换"
    )
    if mode == "locked":
        return CameraPlan(
            mode=mode,
            motivation=motivation,
            action_axis=action_axis,
            screen_direction=screen_direction,
            start_position=composition,
            camera_beats=[
                CameraBeat(
                    phase="opening",
                    trajectory="锁定机位，摄影机保持完全静止",
                    framing="依靠人物视线、手势、身体重心和画内走位保持构图活力",
                    parallax="不制造摄影机视差；前中远景和环境锚点保持固定",
                ),
                CameraBeat(
                    phase="resolution",
                    trajectory="继续锁定机位，让表情和动作结果至少停留一拍",
                    framing="不重新构图、不推拉、不环绕，清楚保留最终反应",
                    parallax="背景结构、人物屏幕位置和空间轴线保持稳定",
                ),
            ],
            end_position="与起始机位相同的稳定机位",
        )
    dramatic_function = shot.shot_intent.dramatic_function
    if dramatic_function in {"pressure", "payoff", "cliffhanger"}:
        push_amount = "约5%" if mode == "motivated_subtle" else "约8%"
        return CameraPlan(
            mode=mode,
            motivation=motivation,
            action_axis=action_axis,
            screen_direction=screen_direction,
            start_position=composition,
            camera_beats=[
                CameraBeat(
                    phase="opening",
                    trajectory=f"由关键信息或情绪落点触发，沿行动轴同侧极慢推近{push_amount}",
                    framing="只收紧到更清楚的面部或手部反应，不改变屏幕侧",
                    parallax=f"{location}近景层产生轻微视差，批准的固定空间锚点方向不变",
                ),
                CameraBeat(
                    phase="resolution",
                    trajectory="唯一一次推近完成后减速停住，不追加横移、环绕或升降",
                    framing="在最终表情或动作结果上停留至少一拍",
                    parallax="背景运动自然收束，光源和空间锚点不换边",
                ),
            ],
            end_position="行动轴同侧略收紧的稳定机位，为下一镜保留视线接点",
        )
    amplitude = "短距离" if mode == "motivated_subtle" else "明确但克制的短距离"
    return CameraPlan(
        mode=mode,
        motivation=motivation,
        action_axis=action_axis,
        screen_direction=screen_direction,
        start_position=composition,
        camera_beats=[
            CameraBeat(
                phase="opening",
                trajectory=f"由人物位移或信息揭示触发，沿行动轴同侧{amplitude}横移一次",
                framing="只跟随主要动作重新构图，保留动作方向一侧的空间",
                parallax=f"{location}近处固定空间锚点移动较快，远处背景移动较慢",
            ),
            CameraBeat(
                phase="resolution",
                trajectory="唯一一次横移完成后减速停住，不再追加环绕、推进或升降",
                framing="停在能读清最终表情与动作结果的位置，并保持至少一拍",
                parallax="横向位移逐渐停止，背景运动自然收束",
            ),
        ],
        end_position="行动轴同侧的稳定机位，为下一镜保留方向一致的衔接",
    )


def _action_physics_plan_for(shot, turn: ScriptTurn) -> ActionPhysicsPlan | None:
    text = " ".join(
        filter(None, (shot.visual_prompt, shot.motion_prompt, turn.text))
    )
    action_tokens = (
        "碎", "裂", "撞", "击", "拳", "爆", "冲", "跑", "追",
        "投", "扔", "推开", "拉开", "跌", "倒", "挥", "劈",
    )
    if not any(token in text for token in action_tokens):
        return None
    if any(token in text for token in ("碎", "裂")):
        return ActionPhysicsPlan(
            trigger="人物对道具施加持续压力",
            preparation="手指与掌心先稳定接触唯一道具，手腕保持承力",
            force="手指逐渐收紧，力量从掌心向接触面集中",
            contact="裂纹从真实接触点向外扩散",
            reaction="道具破裂后手腕受到轻微反作用，不挥臂",
            settling="碎片和粉末受重力向下落定，人物保持原站位",
            environment_feedback=["桌面或地面接住少量碎片", "衣袖只产生低幅滞后"],
        )
    energy_actions = (
        "战气爆发", "战气外放", "战气汇聚", "战气涌出", "释放战气",
        "能量爆发", "能量外放", "能量汇聚", "气旋凝聚",
    )
    if any(token in text for token in energy_actions):
        return ActionPhysicsPlan(
            trigger="人物力量状态发生明确变化",
            preparation="重心先稳定，肩背和衣料仍处于平静状态",
            force="能量从躯干中心沿单一方向向外扩散",
            contact="能量边缘先作用于近身衣摆和发梢",
            reaction="衣摆、发梢和近处轻尘按同一方向延迟响应",
            settling="能量强度停止增长并保持可读轮廓",
            environment_feedback=["近处轻尘向外移动后沉降", "固定光源方向不改变"],
        )
    if any(token in text for token in ("跑", "追", "冲")):
        return ActionPhysicsPlan(
            trigger="人物决定快速位移",
            preparation="重心先移向支撑脚，上身朝运动方向倾斜",
            force="支撑脚蹬地，步频与步幅逐渐增加",
            contact="脚掌按路径连续接触地面，不滑移穿地",
            reaction="衣摆和头发晚于身体响应，随速度形成同向滞后",
            settling="人物减速并在明确位置重新站稳",
            environment_feedback=["脚步反馈匹配地面材质", "运动方向跨镜保持"],
        )
    return ActionPhysicsPlan(
        trigger="剧情事件触发一次主要身体或道具动作",
        preparation="人物先调整视线、接触和身体重心",
        force="力量沿一个明确方向释放",
        contact="手脚或道具在明确位置完成接触",
        reaction="人物、道具和衣料产生幅度克制的反作用",
        settling="动作完成后姿态、道具和环境状态稳定落定",
        environment_feedback=["接触阴影与道具位置保持", "下一镜继承落定状态"],
    )


def _scene_spatial_contract(
    location: AssetRecord,
    location_name: str,
) -> SceneSpatialContract:
    version_id = f"{location.asset_id}@{location.version}"
    time_of_day = location.state_variables.get("time_of_day", "unspecified")
    lighting_source = location.state_variables.get("lighting_source")
    anchors = location.identity_invariants or [f"{location_name}固定建筑与出入口"]
    return SceneSpatialContract(
        location_version_id=version_id,
        time_of_day=time_of_day,
        zones={
            "foreground": "可用于尺度或过肩关系，不自动增加人物",
            "action_zone": "主要表演、对话或道具接触发生区",
            "background": "保留批准建筑锚点，不增加未登记人物",
        },
        anchor_objects=anchors,
        lighting_source=lighting_source
        or (
            "批准场景资产中固定的白昼窗光"
            if "day" in time_of_day
            else "批准场景资产中的固定真实光源"
        ),
        action_axis=f"{location_name}首次建立的对话或运动轴",
        continuity_notes=[
            "换机位不重建房间",
            "屏幕方向变化必须由可见转身或中性镜头解释",
            "未登记状态默认保持",
        ],
    )


def compile_production_plan(
    video_id: str,
    episode: Episode,
    plan: EpisodePlan,
    bible: StoryBible,
    assets: SeriesAssetManifest,
) -> ProductionPlan:
    character_ids = {record.name: record.asset_id for record in assets.characters}
    speaker_slots = {name: index for index, name in enumerate(character_ids)}
    camera_modes = _camera_movement_budget(plan)
    character_specs = {character.name: character for character in bible.characters}
    scenes: list[RuntimeScene] = []
    shots: list[RuntimeShot] = []
    units: list[RuntimeUnit] = []
    current_scene_key: tuple[str, str] | None = None
    scene_index = 0
    for shot in plan.shots:
        if shot.audio_plan.speech_strategy != SpeechStrategy.NATIVE:
            raise ValueError(
                f"shot {shot.index} requests legacy speech; native dialogue is required"
            )
        resolved_characters = visible_character_names_for_shot(
            shot,
            tuple(character_ids),
        )
        if resolved_characters != shot.characters:
            shot = shot.model_copy(update={"characters": resolved_characters})
        location_id = _resolve_location_id(shot.location, assets)
        location_record = next(
            record for record in assets.locations if record.asset_id == location_id
        )
        location_name = shot.location or location_record.name
        scene_key = (location_id, shot.scene_job)
        if scene_key != current_scene_key:
            scene_index += 1
            current_scene_key = scene_key
            scenes.append(
                RuntimeScene(
                    scene_id=f"scene_{scene_index:03d}",
                    index=scene_index,
                    location_asset_id=location_id,
                    narrative_job=shot.scene_job,
                    shot_ids=[],
                    spatial_contract=_scene_spatial_contract(
                        location_record,
                        location_name,
                    ),
                )
            )
        scene = scenes[-1]
        shot_id = f"shot_{shot.index:03d}"
        unit_ids = []
        shot_turns = _turns_for_shot(shot)
        shot_performance_plan = _materialize_performance_timing(
            shot,
            _performance_plan_for(shot, shot_turns[0]),
        )
        performance_assignments = _assign_motion_beat_indexes(
            shot_performance_plan,
            len(shot_turns),
        )
        for turn_index, turn in enumerate(shot_turns, start=1):
            unit_id = f"{shot_id}_turn_{turn_index:02d}"
            source_quote = turn.source_quote or shot.source_quote
            if "".join(source_quote.split()) not in "".join(episode.source_text.split()):
                source_quote = shot.source_quote
            if "".join(source_quote.split()) not in "".join(episode.source_text.split()):
                raise ValueError(f"{unit_id} source_quote is not grounded in the source episode")
            if turn.speaking and turn.speaker_name not in character_ids:
                raise ValueError(
                    f"{unit_id} visible speaker {turn.speaker_name!r} has no locked character asset"
                )
            if (
                turn.role != "narrator"
                and turn.derivation == TurnDerivation.VERBATIM
                and "".join(turn.text.split()) not in "".join(source_quote.split())
            ):
                # Derived turns stage narration as speech; their citation is
                # the narration itself (grounded above), so the verbatim
                # containment that applies to quoted lines cannot hold.
                raise ValueError(
                    f"{unit_id} character utterance is not an exact substring of its grounded source quote"
                )
            visible_character_ids = [
                character_ids[name] for name in shot.characters if name in character_ids
            ]
            if (
                turn.speaking
                and turn.speaker_name in character_ids
                and shot.visual_strategy != VisualStrategy.STORY_KEYFRAME
            ):
                speaker_id = character_ids[turn.speaker_name]
                # Dialogue units are deliberately single-speaker close-ups.
                # Other shot characters remain represented by their own turns,
                # but must not contaminate this unit's identity reference.
                visible_character_ids = [speaker_id]
            visible_character_ids = list(dict.fromkeys(visible_character_ids))
            voice = assets.voice_assignments.get(
                turn.speaker_name if turn.role != "narrator" else "narrator",
                assets.voice_assignments["narrator"],
            )
            composition = (
                _dialogue_composition(
                    shot.index,
                    turn_index,
                    turn.speaker_name,
                    speaker_slots.get(turn.speaker_name),
                )
                if turn.speaking
                else f"{shot.shot_scale}竖屏构图，场景具有前景、中景和远景层次"
            )
            if turn.speaking:
                visual_context = _dialogue_visual_context(shot, turn.speaker_name)
                if (
                    turn.speaker_name in visual_context
                    and "画面左侧" in visual_context
                    and "看向右" in visual_context
                ):
                    composition = _DIALOGUE_AXIS_COMPOSITIONS[1][0]
                elif (
                    turn.speaker_name in visual_context
                    and "画面右侧" in visual_context
                    and "看向左" in visual_context
                ):
                    composition = _DIALOGUE_AXIS_COMPOSITIONS[0][0]
            beat_indexes = performance_assignments[turn_index - 1]
            default_actor = (
                turn.speaker_name
                if turn.role != "narrator"
                else next(iter(shot.characters), "环境")
            )
            performance_plan = _performance_plan_for_unit(
                shot_performance_plan,
                beat_indexes,
                actor=default_actor,
            )
            planned_seconds = _planned_unit_seconds(turn, performance_plan)
            performance_start_state = (
                performance_plan.start_state
                if performance_plan is not None
                else "承接上一单元动作终点，人物与道具位置不重置"
            )
            performance_end_state = (
                performance_plan.end_state
                if performance_plan is not None
                else "保持承接状态完成本句，不新增与剧情无关的动作"
            )
            action_physics_plan = _action_physics_plan_for(shot, turn)
            camera_mode, camera_motivation = camera_modes[shot.index]
            camera_plan = _camera_plan_for(
                shot,
                turn,
                composition,
                forced_mode=camera_mode,
                forced_motivation=camera_motivation,
            )
            intent_contract = (
                f"【镜头戏剧意图】功能={shot.shot_intent.dramatic_function}；"
                f"权力关系={shot.shot_intent.power_relation}；"
                f"目标情绪={shot.shot_intent.emotion_target}；"
                f"观众焦点={shot.shot_intent.viewer_focus}；"
                f"留存节点={shot.shot_intent.retention_beat_id or '未绑定'}。"
            )
            if turn.speaking:
                speaker = character_specs[turn.speaker_name]
                actor_identity = _character_identity(speaker)
                story_keyframe_characters = [
                    character_specs[name]
                    for name in shot.characters
                    if name in character_specs
                ]
                is_multi_character_keyframe = (
                    shot.visual_strategy == VisualStrategy.STORY_KEYFRAME
                    and len(story_keyframe_characters) >= 2
                )
                if is_multi_character_keyframe:
                    identity_rule = (
                        "参考图分别锁定以下具名角色的身份、服装和画风："
                        + "；".join(
                            _character_identity(character)
                            for character in story_keyframe_characters
                        )
                        + "；不得交换身份、服装或空间位置。"
                    )
                    subject_rule = (
                        f"恰好画出{len(story_keyframe_characters)}名具名角色："
                        + "、".join(
                            character.name for character in story_keyframe_characters
                        )
                        + f"；只有{turn.speaker_name}说话，其余角色闭嘴并只做剧情要求的反应。"
                    )
                else:
                    identity_rule = (
                        f"参考图只锁定唯一角色身份、服装和画风：{actor_identity}；"
                        "不要复制参考图中的静态姿势、画面位置或摄影机构图。"
                    )
                    subject_rule = (
                        f"只画{turn.speaker_name}单人，情绪为{turn.emotion}，"
                        "脸和完整嘴部位于竖屏安全区，嘴巴自然闭合且无遮挡。"
                        "不得出现被对话者、其他前景人物、文字、字幕、气泡、Logo或水印。"
                    )
                keyframe_contract = (
                    "【剧情锚点关键帧】原因："
                    + "、".join(shot.keyframe_reasons or ["导演指定构图"])
                    + "。"
                    if str(shot.visual_strategy) == "story-keyframe"
                    else "【常规单人镜头】构图服务这一句台词的表演，不要堆叠额外道具、人物或信息。"
                )
                keyframe_prompt = (
                    keyframe_contract
                    +
                    f"系列风格指纹 {bible.style_fingerprint}。视觉风格：{bible.visual_style}。"
                    f"{intent_contract}"
                    f"{identity_rule}"
                    f"场景明确为{shot.location or assets.locations[0].name}，背景适度虚化。"
                    f"分镜视觉约束：{visual_context}。"
                    f"人物即将表达的剧情信息是“{turn.text}”，只把语义转化为表情、视线和动作，"
                    "画面中不得出现这句文字。"
                    f"这是动作发生前一瞬的可运动起始帧：{performance_start_state}。"
                    f"摄影机起始位置：{camera_plan.start_position}。{camera_plan.screen_direction}。"
                    "画面必须为人物后续动作保留空间，"
                    f"不得提前画出动作终点“{performance_end_state}”。"
                    f"{subject_rule}"
                )
                motion_prompt = build_sd_prompt(
                    turn.speaker_name,
                    turn.text,
                    shot.motion_prompt,
                    actor_description=actor_identity,
                    composition_prompt=composition,
                    emotion=turn.emotion,
                    performance_plan=performance_plan,
                    camera_plan=camera_plan,
                    shot_intent=shot.shot_intent,
                    audio_plan=shot.audio_plan,
                )
            else:
                actor_identity = None
                keyframe_contract = (
                    "【剧情锚点关键帧】原因："
                    + "、".join(shot.keyframe_reasons or ["导演指定构图"])
                    + "。"
                    if str(shot.visual_strategy) == "story-keyframe"
                    else "【常规场景镜头】构图服务当前这一拍，不要堆叠额外道具、人物或信息。"
                )
                keyframe_prompt = (
                    keyframe_contract
                    +
                    f"系列风格指纹 {bible.style_fingerprint}。参考板只锁定场景、角色身份、服装、色彩和光线；"
                    f"{intent_contract}"
                    "不要复制参考板中的静态姿势和摄影机构图。"
                    f"分镜视觉约束：{shot.visual_prompt}。"
                    f"当前叙事信息是“{turn.text}”，只用人物行为、道具和场景状态表达，"
                    "画面中不得出现这句文字。"
                    f"这是动作发生前一瞬的起始帧：{performance_start_state}。"
                    f"摄影机起始位置：{camera_plan.start_position}。{camera_plan.screen_direction}。"
                    "必须为后续人物动作保留空间，"
                    f"不要提前画出动作终点“{performance_end_state}”。所有人物嘴巴自然闭合。"
                    "同一角色在现实空间只允许出现一个实例，禁止分身、重复人物、多人设定稿拼贴；"
                    "镜面场景只允许本人及一个严格对应的倒影，道具特写允许人物不出镜。"
                    f"竖屏国漫构图，视觉风格严格遵循：{bible.visual_style}。"
                    "不要文字、气泡、Logo、水印，不改变人物身份。"
                    "所有人物皮肤完整洁净、衣物完整洁净，画面健康克制；"
                    "涉及危险线索时只用人物反应与非伤害性道具表达。"
                )
                motion_prompt = build_sd_prompt(
                    "narrator",
                    turn.text,
                    shot.motion_prompt,
                    composition_prompt=composition,
                    performance_plan=performance_plan,
                    camera_plan=camera_plan,
                    shot_intent=shot.shot_intent,
                    audio_plan=shot.audio_plan,
                )
            units.append(
                RuntimeUnit(
                    unit_id=unit_id,
                    episode_id=video_id,
                    scene_id=scene.scene_id,
                    shot_id=shot_id,
                    shot_index=shot.index,
                    turn_index=turn_index,
                    role=turn.role,
                    speaker_name=turn.speaker_name,
                    speaking=turn.speaking,
                    delivery_mode=turn.delivery_mode or TurnDelivery.NARRATION,
                    text=turn.text,
                    emotion=turn.emotion,
                    source_quote=source_quote,
                    character_asset_ids=visible_character_ids,
                    location_asset_id=location_id,
                    location_name=location_name,
                    voice=voice,
                    visual_prompt=shot.visual_prompt,
                    motion_instruction=shot.motion_prompt,
                    motion_prompt=motion_prompt,
                    keyframe_prompt=keyframe_prompt,
                    actor_description=actor_identity,
                    composition_prompt=composition,
                    performance_plan=performance_plan,
                    camera_plan=camera_plan,
                    visual_strategy=shot.visual_strategy,
                    keyframe_reasons=shot.keyframe_reasons,
                    shot_intent=shot.shot_intent,
                    audio_plan=shot.audio_plan,
                    action_physics_plan=action_physics_plan,
                    script_open_state=shot.script_open_state,
                    script_close_state=shot.script_close_state,
                    planned_seconds=planned_seconds,
                    performance_beat_indexes=beat_indexes,
                    keyframe_path=f"work/keyframes/{unit_id}.jpeg",
                    raw_video_path=f"work/raw_video/{unit_id}.mp4",
                    segment_path=f"work/segments/{unit_id}.mp4",
                )
            )
            unit_ids.append(unit_id)
        shots.append(
            RuntimeShot(
                shot_id=shot_id,
                scene_id=scene.scene_id,
                index=shot.index,
                narrative_job=shot.scene_job,
                location_asset_id=location_id,
                source_quote=shot.source_quote,
                shot_intent=shot.shot_intent,
                unit_ids=unit_ids,
            )
        )
        scene.shot_ids.append(shot_id)
    units_by_scene: dict[str, list[RuntimeUnit]] = {}
    for unit in units:
        units_by_scene.setdefault(unit.scene_id, []).append(unit)
    for scene in scenes:
        if scene.spatial_contract is None:
            continue
        scene.spatial_contract = scene.spatial_contract.model_copy(
            update={
                "allowed_asset_ids": list(
                    dict.fromkeys(
                        asset_id
                        for unit in units_by_scene.get(scene.scene_id, [])
                        for asset_id in unit.character_asset_ids
                    )
                )
            }
        )
    return ProductionPlan(
        video_id=video_id,
        source_title=episode.source_title,
        source_text_sha256=sha256_text(episode.source_text),
        style_fingerprint=bible.style_fingerprint,
        visual_style=bible.visual_style,
        palette=bible.palette,
        scenes=scenes,
        shots=shots,
        units=units,
    )


def _camera_movement_budget(plan: EpisodePlan) -> dict[int, tuple[str, str]]:
    """Normalize legacy/model camera plans before compiling media requests.

    Preserve explicit and story-motivated subtle moves.  Only emphasis moves
    consume a sparse budget; a slow push or follow shot is not a spectacle and
    must not be disabled merely because the neighbouring shot also moves.
    """

    emphasis_budget = max(1, math.floor(len(plan.shots) * 0.20))
    normalized_modes: dict[int, tuple[str, str]] = {}
    emphasis = 0
    for shot in plan.shots:
        proposed_mode, motivation, _ = _camera_mode_for(
            shot,
            _turns_for_shot(shot)[0],
        )
        selected_mode = proposed_mode
        if selected_mode == "motivated_emphasis" and emphasis >= emphasis_budget:
            selected_mode = "motivated_subtle"
            motivation = f"{motivation}；受强调运镜预算约束，降为克制短移"
        normalized_modes[shot.index] = (selected_mode, motivation)
        if selected_mode == "motivated_emphasis":
            emphasis += 1
    return normalized_modes
