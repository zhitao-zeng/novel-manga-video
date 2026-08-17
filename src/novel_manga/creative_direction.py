from __future__ import annotations

from dataclasses import dataclass

from .models import (
    ChapterDiagnosis,
    EpisodeDramaturgy,
    EpisodePlan,
    SceneAudioPlan,
    Shot,
    SpeechStrategy,
    StoryBible,
    VisualStrategy,
)


SHORT_DRAMA_PROFILE = "short-drama-adaptive-v1"
FAITHFUL_PROFILE = "faithful-chronological-v1"


@dataclass(frozen=True)
class GenreDirection:
    engine: str
    narration_budget_ratio: float
    story_engine: str
    sound_direction: str


_GENRE_DIRECTIONS = {
    "romance": GenreDirection(
        engine="relationship-misread",
        narration_budget_ratio=0.12,
        story_engine="关系误判→细节露馅→情绪试探→暧昧反转",
        sound_direction="近距离对白、克制心跳、轻喜反应音和连续环境底",
    ),
    "xuanhuan": GenreDirection(
        engine="status-power-mystery",
        narration_budget_ratio=0.20,
        story_engine="公开受压→身份反差→能力谜团→异象或反击",
        sound_direction="低频能量、群体议论、结果冲击和神秘尾音",
    ),
    "revenge": GenreDirection(
        engine="humiliation-counterattack",
        narration_budget_ratio=0.14,
        story_engine="不公平事件→主角隐忍→对手加码→公开反击",
        sound_direction="压迫底乐、停顿、身份揭示冲击和人群反应",
    ),
    "suspense": GenreDirection(
        engine="evidence-reinterpretation",
        narration_budget_ratio=0.24,
        story_engine="异常细节→错误解释→新证据→原判断被推翻",
        sound_direction="声音线索、留白、环境异响和低频悬念",
    ),
    "growth": GenreDirection(
        engine="failure-progress",
        narration_budget_ratio=0.20,
        story_engine="能力缺口→失败代价→主动选择→微小进展",
        sound_direction="呼吸、动作质感、节奏递进和克制回报音乐",
    ),
}


_KEYFRAME_INTERACTION_MARKERS = (
    "递给",
    "交给",
    "抓住",
    "拉住",
    "拥抱",
    "亲吻",
    "打斗",
    "交手",
    "碰撞",
    "夺走",
    "砸",
    "击中",
)
_KEYFRAME_REVEAL_MARKERS = (
    "反转",
    "揭示",
    "结果",
    "真相",
    "身份",
    "测试",
    "测验",
    "戒指",
    "纹身",
    "证件照",
    "特写",
    "高潮",
    "悬念",
)


def genre_direction(genre: str) -> GenreDirection:
    value = genre.casefold()
    if any(token in value for token in ("甜宠", "言情", "恋爱", "爱情", "校园")):
        return _GENRE_DIRECTIONS["romance"]
    if any(token in value for token in ("玄幻", "仙侠", "修真", "奇幻", "武侠")):
        return _GENRE_DIRECTIONS["xuanhuan"]
    if any(token in value for token in ("复仇", "逆袭", "赘婿", "都市")):
        return _GENRE_DIRECTIONS["revenge"]
    if any(token in value for token in ("悬疑", "推理", "惊悚", "刑侦")):
        return _GENRE_DIRECTIONS["suspense"]
    return _GENRE_DIRECTIONS["growth"]


def infer_visual_strategy(shot: Shot) -> tuple[VisualStrategy, list[str]]:
    """Choose the cheapest safe visual input without hiding the rationale."""

    if shot.visual_strategy != VisualStrategy.AUTO:
        return shot.visual_strategy, list(dict.fromkeys(shot.keyframe_reasons))
    interaction_haystack = "|".join(
        (
            shot.scene_job,
            shot.visual_prompt,
            shot.motion_prompt,
            shot.narration,
        )
    )
    reveal_haystack = "|".join(
        (shot.scene_job, shot.visual_prompt, shot.narration)
    )
    reasons = list(shot.keyframe_reasons)
    if len(shot.characters) > 1:
        reasons.append("multi-character-blocking")
    if any(marker in interaction_haystack for marker in _KEYFRAME_INTERACTION_MARKERS):
        reasons.append("precise-interaction")
    if any(marker in reveal_haystack for marker in _KEYFRAME_REVEAL_MARKERS):
        reasons.append("plot-reveal-or-prop")
    if shot.camera_plan is not None and shot.camera_plan.mode == "motivated_emphasis":
        reasons.append("emphasis-composition")
    reasons = list(dict.fromkeys(reasons))
    if reasons:
        return VisualStrategy.STORY_KEYFRAME, reasons
    if not shot.characters and not any(turn.speaking for turn in shot.turns):
        return VisualStrategy.SCENE_ONLY, []
    if len(shot.characters) <= 1:
        return VisualStrategy.DIRECT_ASSETS, []
    return VisualStrategy.STORY_KEYFRAME, ["unresolved-cast-layout"]


def _default_dramaturgy(
    diagnosis: ChapterDiagnosis,
    bible: StoryBible,
) -> EpisodeDramaturgy:
    direction = genre_direction(bible.genre)
    conflict = [
        event.description
        for event in diagnosis.events
        if event.importance == "critical"
    ][:5]
    return EpisodeDramaturgy(
        genre_engine=direction.engine,
        dramatic_question=diagnosis.strongest_hook_candidate,
        cold_open=diagnosis.strongest_hook_candidate,
        cold_open_source_quote=diagnosis.hook_source_quote,
        status_before=diagnosis.chapter_start_state,
        status_after=diagnosis.chapter_end_state,
        conflict_beats=conflict or [diagnosis.core_event],
        reveal_order=[event.event_id for event in diagnosis.events if event.importance == "critical"],
        cliffhanger=diagnosis.chapter_end_state,
        narration_budget_ratio=direction.narration_budget_ratio,
    )


def apply_creative_direction(
    plan: EpisodePlan,
    diagnosis: ChapterDiagnosis,
    bible: StoryBible,
    *,
    profile: str,
) -> EpisodePlan:
    """Attach an auditable directing decision without changing source facts."""

    if profile == FAITHFUL_PROFILE:
        return plan.model_copy(update={"creative_profile": profile})
    if profile != SHORT_DRAMA_PROFILE:
        raise ValueError(f"unsupported creative profile: {profile}")
    direction = genre_direction(bible.genre)
    dramaturgy = plan.dramaturgy or _default_dramaturgy(diagnosis, bible)
    dramaturgy = dramaturgy.model_copy(
        update={
            "genre_engine": direction.engine,
            "narration_budget_ratio": min(
                dramaturgy.narration_budget_ratio,
                direction.narration_budget_ratio,
            ),
        }
    )
    shots = []
    for shot in plan.shots:
        visual_strategy, reasons = infer_visual_strategy(shot)
        # The runtime currently guarantees exact source dialogue through
        # locked speech. Non-speech layers are separate directing cues, not
        # synthetic TTS utterances. Native speech remains a schema-level future
        # mode until the API exposes a verifiable full-audio or stem contract.
        audio_plan = shot.audio_plan.model_copy(
            update={
                "speech_strategy": SpeechStrategy.LOCKED,
                "delivery_intent": shot.audio_plan.delivery_intent or "克制自然",
                "ambience": shot.audio_plan.ambience or direction.sound_direction,
            }
        )
        shots.append(
            shot.model_copy(
                update={
                    "visual_strategy": visual_strategy,
                    "keyframe_reasons": reasons,
                    "audio_plan": audio_plan,
                }
            )
        )
    return plan.model_copy(
        update={
            "creative_profile": profile,
            "dramaturgy": dramaturgy,
            "shots": shots,
        }
    )


def creative_prompt_brief(genre: str) -> str:
    direction = genre_direction(genre)
    return (
        f"题材发动机={direction.engine}；节拍={direction.story_engine}；"
        f"旁白字数占比上限={direction.narration_budget_ratio:.0%}；"
        f"声音方向={direction.sound_direction}。"
    )
