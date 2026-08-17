from __future__ import annotations

from dataclasses import dataclass

from .models import (
    AudioBeat,
    CharacterAwareness,
    CharacterDramaticState,
    CharacterStateDelta,
    ChapterDiagnosis,
    EpisodeDramaturgy,
    EpisodePlan,
    InformationState,
    RetentionBeat,
    RetentionPlan,
    SceneAudioPlan,
    ShotIntent,
    ShowrunnerPlan,
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
    if (
        shot.shot_intent.information_fact_ids
        and shot.shot_intent.dramatic_function in {"reveal", "payoff", "cliffhanger"}
    ):
        reasons.append("information-reveal")
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


def _event_for_shot(shot: Shot, diagnosis: ChapterDiagnosis):
    events = {event.event_id: event for event in diagnosis.events}
    return next(
        (events[event_id] for event_id in shot.event_ids if event_id in events),
        diagnosis.events[0],
    )


def _default_showrunner_plan(
    plan: EpisodePlan,
    diagnosis: ChapterDiagnosis,
) -> ShowrunnerPlan:
    """Build a conservative source-grounded fallback for non-LLM planners."""

    count = len(plan.shots)

    def shot_at(position: float) -> Shot:
        index = min(count - 1, max(0, round((count - 1) * position)))
        return plan.shots[index]

    beat_specs = (
        ("hook", 0.00, 0.05, 0.00),
        ("question", 0.08, 0.18, 0.18),
        ("escalation", 0.32, 0.50, 0.45),
        ("payoff", 0.56, 0.74, 0.72),
        ("cliffhanger", 0.80, 1.00, 1.00),
    )
    beats: list[RetentionBeat] = []
    for index, (function, start, end, position) in enumerate(beat_specs, 1):
        shot = shot_at(position)
        event = _event_for_shot(shot, diagnosis)
        beats.append(
            RetentionBeat(
                beat_id=f"beat_{index:03d}",
                function=function,
                target_start_ratio=start,
                target_end_ratio=end,
                audience_question=(
                    plan.dramaturgy.dramatic_question
                    if plan.dramaturgy is not None
                    else diagnosis.strongest_hook_candidate
                ),
                promise=(
                    "在当前章内兑现冷开场的原因与后果"
                    if function != "cliffhanger"
                    else "留下当前章边界上的未解决后果"
                ),
                emotional_shift=f"观众从上一状态进入{function}节拍",
                event_ids=list(shot.event_ids) or [event.event_id],
                shot_indexes=[shot.index],
                source_quote=shot.source_quote or event.source_quote,
            )
        )
    cold_event = _event_for_shot(plan.shots[0], diagnosis)
    information = InformationState(
        fact_id="fact_001",
        statement=(
            plan.dramaturgy.cold_open
            if plan.dramaturgy is not None
            else diagnosis.strongest_hook_candidate
        ),
        truth_status="confirmed",
        viewer_awareness="knows",
        character_awareness=[
            CharacterAwareness(
                character_name=name,
                awareness="knows",
                belief="亲历或目睹当前章事件",
            )
            for name in cold_event.characters
        ],
        dramatic_use="simultaneous_reveal",
        source_event_ids=list(plan.shots[0].event_ids) or [cold_event.event_id],
        source_quote=plan.shots[0].source_quote or cold_event.source_quote,
        reveal_beat_id="beat_001",
    )
    beats[0] = beats[0].model_copy(
        update={"new_information_fact_ids": [information.fact_id]}
    )
    deltas: list[CharacterStateDelta] = []
    for event in diagnosis.events:
        if not event.state_change or not event.characters:
            continue
        for character_name in event.characters:
            deltas.append(
                CharacterStateDelta(
                    character_name=character_name,
                    event_ids=[event.event_id],
                    before=CharacterDramaticState(
                        social_status=diagnosis.chapter_start_state,
                        emotional_state="事件发生前状态",
                    ),
                    after=CharacterDramaticState(
                        social_status=event.state_change,
                        emotional_state="事件结果后的状态",
                    ),
                    source_quote=event.source_quote,
                    visual_consequence="只改变本集可见的姿态、表情或有原文依据的服装状态",
                    performance_consequence="表演从事件前状态过渡到原文明确的结果状态",
                )
            )
    return ShowrunnerPlan(
        planning_mode="inferred_fallback",
        retention=RetentionPlan(
            target_duration_seconds=max(20.0, min(180.0, count * 6.0)),
            max_attention_gap_ratio=0.25,
            beats=beats,
            ending_open_loop=(
                plan.dramaturgy.cliffhanger
                if plan.dramaturgy is not None
                else diagnosis.chapter_end_state
            ),
        ),
        information_states=[information],
        character_state_deltas=deltas[:12],
    )


def bind_showrunner_to_shots(
    showrunner: ShowrunnerPlan,
    plan: EpisodePlan,
) -> ShowrunnerPlan:
    """Resolve event-level Showrunner beats to actual screenplay shot indexes."""

    beats = []
    for beat in showrunner.retention.beats:
        indexes = [
            shot.index
            for shot in plan.shots
            if set(shot.event_ids) & set(beat.event_ids)
        ]
        beats.append(
            beat.model_copy(update={"shot_indexes": list(dict.fromkeys(indexes))})
        )
    return showrunner.model_copy(
        update={
            "retention": showrunner.retention.model_copy(update={"beats": beats})
        }
    )


_RETENTION_TO_SHOT_FUNCTION = {
    "hook": "reveal",
    "question": "withhold",
    "pressure": "pressure",
    "escalation": "pressure",
    "payoff": "payoff",
    "reversal": "reveal",
    "cliffhanger": "cliffhanger",
}


def _intent_for_shot(shot: Shot, showrunner: ShowrunnerPlan) -> ShotIntent:
    beats_by_id = {
        candidate.beat_id: candidate for candidate in showrunner.retention.beats
    }
    beat = beats_by_id.get(shot.shot_intent.retention_beat_id) or next(
        (
            candidate
            for candidate in showrunner.retention.beats
            if shot.index in candidate.shot_indexes
        ),
        min(
            showrunner.retention.beats,
            key=lambda candidate: min(
                abs(shot.index - index) for index in candidate.shot_indexes
            ),
        ),
    )
    fact_ids = list(
        dict.fromkeys(
            [*shot.shot_intent.information_fact_ids, *beat.new_information_fact_ids]
        )
    )
    return shot.shot_intent.model_copy(
        update={
            "dramatic_function": (
                _RETENTION_TO_SHOT_FUNCTION.get(beat.function, "advance")
                if shot.shot_intent.dramatic_function == "advance"
                else shot.shot_intent.dramatic_function
            ),
            "power_relation": (
                "按当前章人物地位与关系呈现，不额外强化"
                if shot.shot_intent.power_relation == "未明确"
                else shot.shot_intent.power_relation
            ),
            "emotion_target": (
                beat.emotional_shift
                if shot.shot_intent.emotion_target == "保持关注"
                else shot.shot_intent.emotion_target
            ),
            "information_fact_ids": fact_ids,
            "viewer_focus": (
                shot.visual_prompt[:160]
                if shot.shot_intent.viewer_focus == "当前主要动作与反应"
                else shot.shot_intent.viewer_focus
            ),
            "retention_beat_id": beat.beat_id,
        }
    )


def _audio_beats_for_shot(
    shot: Shot,
    intent: ShotIntent,
    ambience: str,
) -> list[AudioBeat]:
    if shot.audio_plan.audio_beats:
        return [
            beat.model_copy(
                update={
                    "retention_beat_id": beat.retention_beat_id
                    or intent.retention_beat_id
                }
            )
            for beat in shot.audio_plan.audio_beats
        ]
    beats = [
        AudioBeat(
            position_ratio=0.0,
            cue_type="ambience",
            cue=ambience,
            trigger="镜头建立当前空间",
            retention_beat_id=intent.retention_beat_id,
        )
    ]
    if intent.dramatic_function in {"reveal", "payoff", "cliffhanger"}:
        beats.append(
            AudioBeat(
                position_ratio=0.18,
                cue_type="impact" if intent.dramatic_function != "cliffhanger" else "bass_drop",
                cue="在关键信息或结果可读的一刻给出短促声音标点",
                trigger=intent.viewer_focus,
                retention_beat_id=intent.retention_beat_id,
            )
        )
    if shot.turns:
        beats.append(
            AudioBeat(
                position_ratio=0.42,
                cue_type="duck",
                cue="锁定台词出现时压低非语言声音",
                trigger="第一个有效发声turn开始",
                retention_beat_id=intent.retention_beat_id,
            )
        )
    beats.append(
        AudioBeat(
            position_ratio=0.9,
            cue_type="release",
            cue="动作或反应落定后留出短暂停顿",
            trigger="本镜主要动作完成",
            retention_beat_id=intent.retention_beat_id,
        )
    )
    return beats


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
    working_plan = plan.model_copy(update={"dramaturgy": dramaturgy})
    showrunner = bind_showrunner_to_shots(
        plan.showrunner_plan or _default_showrunner_plan(working_plan, diagnosis),
        working_plan,
    )
    shots = []
    for shot in plan.shots:
        shot_intent = _intent_for_shot(shot, showrunner)
        directed_shot = shot.model_copy(update={"shot_intent": shot_intent})
        visual_strategy, reasons = infer_visual_strategy(directed_shot)
        # The runtime currently guarantees exact source dialogue through
        # locked speech. Non-speech layers are separate directing cues, not
        # synthetic TTS utterances. Native speech remains a schema-level future
        # mode until the API exposes a verifiable full-audio or stem contract.
        audio_plan = shot.audio_plan.model_copy(
            update={
                "speech_strategy": SpeechStrategy.LOCKED,
                "delivery_intent": shot.audio_plan.delivery_intent or "克制自然",
                "ambience": shot.audio_plan.ambience or direction.sound_direction,
                "audio_beats": _audio_beats_for_shot(
                    shot,
                    shot_intent,
                    shot.audio_plan.ambience or direction.sound_direction,
                ),
            }
        )
        shots.append(
            shot.model_copy(
                update={
                    "visual_strategy": visual_strategy,
                    "keyframe_reasons": reasons,
                    "shot_intent": shot_intent,
                    "audio_plan": audio_plan,
                }
            )
        )
    return plan.model_copy(
        update={
            "creative_profile": profile,
            "dramaturgy": dramaturgy,
            "showrunner_plan": showrunner,
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
