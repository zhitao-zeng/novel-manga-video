from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .models import (
    AdaptationLedgerItem,
    ChapterDiagnosis,
    ChapterEvent,
    CharacterEpisodeState,
    Episode,
    EpisodeEndState,
    EpisodePlan,
    GroundedStateFact,
    ScriptQualityReport,
    ScriptReviewIssue,
    SeriesState,
    StoryBible,
)
from .creative_direction import SHORT_DRAMA_PROFILE


SCRIPT_POLICY_REVISION = "novel-manga-script-v5-semantic-utterance"
# A turn is a complete TTS breath/meaning group, not one subtitle page.  The
# renderer paginates and times subtitles independently after audio alignment.
SHORT_DRAMA_TURN_TARGET_MAX = 36
SHORT_DRAMA_TURN_HARD_MAX = 60


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _quote_key(value: str) -> str:
    """Normalize formatting-only differences without accepting paraphrases."""

    return re.sub(r"[\s，。！？；：、,.!?;:'\"“”‘’（）()《》〈〉…—·-]+", "", value)


def _sentences(value: str) -> list[str]:
    return [
        part
        for part in re.split(r"(?<=[。！？!?；;])|\n+", value)
        if _normalized(part)
    ]


def source_evidence_units(value: str) -> list[str]:
    """Return copy-safe evidence rows for LLM prompts and quote grounding."""

    rows = [row.strip() for row in value.splitlines() if row.strip()]
    return [row[:500] for row in rows]


def _ground_quote(value: str, source_text: str) -> str | None:
    """Recover a real source row when only whitespace/punctuation was changed.

    Semantic rewrites deliberately do not pass this function: the planner must
    repair them by copying from the evidence bank supplied in its prompt.
    """

    normalized = _normalized(value)
    keyed = _quote_key(value)
    if not normalized or not keyed:
        return None
    matches: list[str] = []
    for row in source_evidence_units(source_text):
        row_normalized = _normalized(row)
        row_keyed = _quote_key(row)
        if normalized in row_normalized:
            matches.append(row)
            continue
        if len(keyed) >= 6 and (keyed in row_keyed or row_keyed in keyed):
            matches.append(row)
    return min(matches, key=len) if matches else None


@dataclass(frozen=True)
class ScriptPolicy:
    min_script_chars: int
    min_turns: int
    min_shots: int


def script_policy(
    source_chars: int,
    density: str,
    creative_profile: str = "faithful-chronological-v1",
) -> ScriptPolicy:
    """Scale screenplay floors without punishing genuinely tiny chapters."""

    if creative_profile == SHORT_DRAMA_PROFILE:
        if source_chars <= 200:
            return ScriptPolicy(max(8, round(source_chars * 0.25)), 1, 1)
        if source_chars <= 1200:
            turns = max(4, min(7, math.ceil(source_chars / 180)))
            return ScriptPolicy(min(240, max(60, round(source_chars * 0.18))), turns, max(4, turns - 1))
        if source_chars <= 3000:
            return ScriptPolicy(min(420, max(240, round(source_chars * 0.12))), 8, 8)
        return ScriptPolicy(min(650, max(420, round(source_chars * 0.10))), 12, 10)
    if source_chars <= 200:
        return ScriptPolicy(max(8, round(source_chars * 0.35)), 1, 1)
    if source_chars <= 1200:
        turns = max(3, min(8, math.ceil(source_chars / 120)))
        return ScriptPolicy(min(450, max(80, round(source_chars * 0.35))), turns, turns)
    if source_chars <= 3000:
        floor = min(800, max(500, round(source_chars * 0.28)))
        turns = 12 if density != "sparse" else 10
        return ScriptPolicy(floor, turns, max(10, turns - 2))
    floor = min(1200, max(800, round(source_chars * 0.20)))
    turns = 20 if density == "dense" else 18
    return ScriptPolicy(floor, turns, 14)


def normalize_chronological_plan(
    plan: EpisodePlan,
    diagnosis: ChapterDiagnosis,
    episode: Episode,
) -> EpisodePlan:
    """Create a separate chronological cut without mutating the saved LLM draft."""

    event_order = {event.event_id: event.order for event in diagnosis.events}
    if plan.creative_profile == SHORT_DRAMA_PROFILE:
        # A result-first cold open intentionally repeats its event after the
        # causes are established.  Do not collapse that editorial replay.
        unique = list(plan.shots)
    else:
        unique = []
        seen: set[tuple[tuple[str, ...], tuple[str, ...], str]] = set()
        for shot in plan.shots:
            signature = (
                tuple(shot.event_ids),
                tuple(_normalized(turn.text) for turn in shot.turns),
                _normalized(shot.source_quote),
            )
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(shot)
        unique.sort(
            key=lambda shot: (
                min((event_order.get(event_id, 10**6) for event_id in shot.event_ids), default=10**6),
                shot.index,
            )
        )
    shots = [shot.model_copy(update={"index": index}) for index, shot in enumerate(unique, 1)]
    ledger = []
    for item in plan.adaptation_ledger:
        indexes = [
            shot.index for shot in shots if item.event_id in shot.event_ids
        ]
        ledger.append(item.model_copy(update={"shot_indexes": indexes}))
    updates: dict[str, object] = {
        "shots": shots,
        "adaptation_ledger": ledger,
    }
    if plan.creative_profile != SHORT_DRAMA_PROFILE:
        updates.update(
            {
                "video_title": episode.source_title,
                "hook": diagnosis.events[0].description,
            }
        )
    return plan.model_copy(update=updates)


def validate_chapter_diagnosis(
    diagnosis: ChapterDiagnosis,
    episode: Episode,
    bible: StoryBible,
) -> ChapterDiagnosis:
    known_characters = {character.name for character in bible.characters}
    issues: list[str] = []
    grounded_hook = _ground_quote(diagnosis.hook_source_quote, episode.source_text)
    if grounded_hook is None:
        issues.append("hook_source_quote must be copied from one SOURCE_EVIDENCE row")
    critical_count = 0
    grounded_events: list[ChapterEvent] = []
    for event in diagnosis.events:
        grounded_quote = _ground_quote(event.source_quote, episode.source_text)
        if grounded_quote is None:
            issues.append(
                f"{event.event_id}.source_quote must be copied from one SOURCE_EVIDENCE row"
            )
            grounded_events.append(event)
        else:
            grounded_events.append(event.model_copy(update={"source_quote": grounded_quote}))
        unknown = set(event.characters) - known_characters
        if unknown:
            issues.append(f"{event.event_id} uses unknown characters: {sorted(unknown)}")
        critical_count += event.importance == "critical"
    if not critical_count:
        issues.append("chapter diagnosis must contain at least one critical event")
    if diagnosis.source_chapter != episode.source_title:
        issues.append("source_chapter must equal the parsed chapter title")
    if issues:
        raise ValueError("; ".join(issues))
    assert grounded_hook is not None
    # Keep all evidence as literal source rows so downstream traceability is
    # stable even when a model changes quote marks or punctuation.
    return diagnosis.model_copy(
        update={"hook_source_quote": grounded_hook, "events": grounded_events}
    )


def deterministic_chapter_diagnosis(episode: Episode) -> ChapterDiagnosis:
    chapter_text = episode.source_text.strip()
    if chapter_text.startswith(episode.source_title):
        chapter_text = chapter_text[len(episode.source_title):].lstrip()
    rows = _sentences(chapter_text)
    if not rows:
        rows = [episode.source_text]
    # The deterministic backend keeps every source beat. LLM backends perform
    # semantic consolidation before writing the screenplay.
    events = []
    for index, row in enumerate(rows, 1):
        role = "setup"
        if index == len(rows):
            role = "resolution"
        elif index > len(rows) * 0.75:
            role = "climax"
        elif index > len(rows) * 0.45:
            role = "turning_point"
        elif index > len(rows) * 0.2:
            role = "development"
        events.append(
            ChapterEvent(
                event_id=f"event_{index:03d}",
                order=index,
                description=_normalized(row)[:240],
                source_quote=_normalized(row)[:500],
                importance="critical",
                narrative_role=role,
                causes=[f"event_{index - 1:03d}"] if index > 1 else [],
            )
        )
    chars = len(_normalized(episode.source_text))
    density = "sparse" if chars <= 1200 else "balanced" if chars <= 3000 else "dense"
    return ChapterDiagnosis(
        source_chapter=episode.source_title,
        density=density,
        core_event=events[len(events) // 2].description,
        chapter_start_state=events[0].description,
        chapter_end_state=events[-1].description,
        episode_state_change=events[-1].state_change or events[-1].description,
        strongest_hook_candidate=events[0].description,
        hook_source_quote=events[0].source_quote,
        ending_type="consequence",
        events=events,
    )


def bind_deterministic_events(
    plan: EpisodePlan,
    diagnosis: ChapterDiagnosis,
) -> EpisodePlan:
    shots = []
    for index, shot in enumerate(plan.shots):
        event = diagnosis.events[min(index, len(diagnosis.events) - 1)]
        shots.append(shot.model_copy(update={"event_ids": [event.event_id]}))
    covered = {event_id for shot in shots for event_id in shot.event_ids}
    ledger = [
        AdaptationLedgerItem(
            event_id=event.event_id,
            disposition="preserved" if event.event_id in covered else "removed",
            shot_indexes=[shot.index for shot in shots if event.event_id in shot.event_ids],
            rationale=(
                "由确定性逐句分镜直接保留"
                if event.event_id in covered
                else "确定性回退未形成独立镜头"
            ),
        )
        for event in diagnosis.events
    ]
    return plan.model_copy(update={"shots": shots, "adaptation_ledger": ledger})


def evaluate_script_quality(
    plan: EpisodePlan,
    diagnosis: ChapterDiagnosis,
    episode: Episode,
    *,
    qualitative: ScriptQualityReport | None = None,
    previous_state: SeriesState | None = None,
) -> ScriptQualityReport:
    source_chars = len(_normalized(episode.source_text))
    policy = script_policy(source_chars, diagnosis.density, plan.creative_profile)
    turns = [turn for shot in plan.shots for turn in shot.turns]
    script_chars = sum(len(_normalized(turn.text)) for turn in turns)
    narration_chars = sum(
        len(_normalized(turn.text)) for turn in turns if turn.role == "narrator"
    )
    narration_ratio = narration_chars / script_chars if script_chars else 0.0
    narration_budget = (
        plan.dramaturgy.narration_budget_ratio
        if plan.dramaturgy is not None
        else 1.0
    )
    covered = {event_id for shot in plan.shots for event_id in shot.event_ids}
    events = {event.event_id: event for event in diagnosis.events}
    critical = {event.event_id for event in diagnosis.events if event.importance == "critical"}
    coverage = len(critical & covered) / len(critical) if critical else 0.0
    issues: list[ScriptReviewIssue] = []

    def block(
        code: str,
        message: str,
        *,
        shot_indexes: list[int] | None = None,
        event_ids: list[str] | None = None,
    ) -> None:
        issues.append(
            ScriptReviewIssue(
                code=code,
                severity="blocking",
                message=message,
                shot_indexes=shot_indexes or [],
                event_ids=event_ids or [],
            )
        )

    turn_lengths = [len(_normalized(turn.text)) for turn in turns]
    target_overflow_shots = sorted(
        {
            shot.index
            for shot in plan.shots
            if any(
                len(_normalized(turn.text)) > SHORT_DRAMA_TURN_TARGET_MAX
                for turn in shot.turns
            )
        }
    )
    hard_overflow_shots = sorted(
        {
            shot.index
            for shot in plan.shots
            if any(
                len(_normalized(turn.text)) > SHORT_DRAMA_TURN_HARD_MAX
                for turn in shot.turns
            )
        }
    )
    if hard_overflow_shots:
        block(
            "spoken_turn_too_long",
            f"单个语义发声段不得超过{SHORT_DRAMA_TURN_HARD_MAX}字；"
            f"通常控制在12-{SHORT_DRAMA_TURN_TARGET_MAX}字。只在自然停顿或语义完成处拆分，"
            "字幕分页由对齐层独立完成，不得为两行字幕把一句话切碎，也不得因此增加切镜",
            shot_indexes=hard_overflow_shots,
        )

    if (
        plan.creative_profile == SHORT_DRAMA_PROFILE
        and narration_ratio > narration_budget + 0.05
    ):
        block(
            "narration_budget_exceeded",
            f"旁白占比{narration_ratio:.1%}超过当前题材预算{narration_budget:.1%}；"
            "能表演、能对白或能用反应镜头表达的信息不得继续写成解释性旁白",
        )

    if script_chars < policy.min_script_chars:
        block(
            "script_too_short",
            f"有效剧本{script_chars}字，当前章节至少需要{policy.min_script_chars}字",
        )
    if len(turns) < policy.min_turns:
        block("too_few_turns", f"只有{len(turns)}个turn，至少需要{policy.min_turns}个")
    if len(plan.shots) < policy.min_shots:
        block("too_few_shots", f"只有{len(plan.shots)}镜，至少需要{policy.min_shots}镜")
    missing = sorted(critical - covered)
    if missing:
        block("critical_events_missing", "关键事件未映射到分镜", event_ids=missing)
    unknown_covered = sorted(covered - set(events))
    if unknown_covered:
        block("unknown_event_ids", "分镜引用了章节诊断中不存在的事件", event_ids=unknown_covered)
    ledger = {item.event_id: item for item in plan.adaptation_ledger}
    missing_ledger = sorted(set(events) - set(ledger))
    if missing_ledger:
        block("adaptation_ledger_incomplete", "改编账本未覆盖全部章节事件", event_ids=missing_ledger)
    unknown_ledger = sorted(set(ledger) - set(events))
    if unknown_ledger:
        block("unknown_ledger_events", "改编账本包含不存在的章节事件", event_ids=unknown_ledger)
    ledger_mapping_errors = []
    shots_by_index = {shot.index: set(shot.event_ids) for shot in plan.shots}
    for event_id, item in ledger.items():
        mapped = sorted(
            index for index, event_ids in shots_by_index.items() if event_id in event_ids
        )
        if sorted(item.shot_indexes) != mapped:
            ledger_mapping_errors.append(event_id)
    if ledger_mapping_errors:
        block(
            "adaptation_ledger_mapping_mismatch",
            "改编账本的shot_indexes与实际分镜事件映射不一致",
            event_ids=sorted(ledger_mapping_errors),
        )
    removed_critical = sorted(
        event_id
        for event_id in critical
        if event_id in ledger and ledger[event_id].disposition == "removed"
    )
    if removed_critical:
        block("critical_events_removed", "关键事件不得删除", event_ids=removed_critical)
    occurrences = {
        event_id: [shot.index for shot in plan.shots if event_id in shot.event_ids]
        for event_id in covered
    }
    causal_complete = True
    for event_id in covered:
        if event_id not in events:
            continue
        for cause in events[event_id].causes:
            if cause not in covered:
                causal_complete = False
                continue
            if min(occurrences[event_id]) < min(occurrences[cause]):
                # A result may appear in a short cold open, but the full event
                # still has to be shown again after its cause is established.
                if max(occurrences[event_id]) < min(occurrences[cause]):
                    causal_complete = False
    if not causal_complete:
        block("causal_chain_broken", "分镜包含结果事件但缺少其前置因果事件")
    final_candidates = [
        event for event in diagnosis.events
        if event.narrative_role in {"climax", "resolution"} and event.importance == "critical"
    ]
    required_ending = final_candidates[-1].event_id if final_candidates else diagnosis.events[-1].event_id
    ending_at_boundary = bool(plan.shots and required_ending in plan.shots[-1].event_ids)
    if not ending_at_boundary:
        block(
            "ending_not_at_chapter_boundary",
            "最后一镜没有落在当前章最后的关键高潮或结果",
            event_ids=[required_ending],
        )

    opening_no_spoiler = True
    cold_open_grounded = True
    introductions_complete = True
    future_content_used = False
    if qualitative is not None:
        opening_no_spoiler = qualitative.opening_no_spoiler
        introductions_complete = qualitative.character_introductions_complete
        future_content_used = qualitative.future_content_used
        issues.extend(
            issue
            for issue in qualitative.issues
            if not (
                plan.creative_profile == SHORT_DRAMA_PROFILE
                and issue.code == "opening_spoils_resolution"
            )
        )
        if not qualitative.passed and not any(
            issue.severity == "blocking" for issue in qualitative.issues
        ):
            block(
                "independent_review_failed",
                "独立审稿未通过，但审稿结果没有提供可执行的blocking问题",
            )
    elif plan.creative_profile != SHORT_DRAMA_PROFILE:
        late_sensitive = {
            event.event_id
            for event in diagnosis.events
            if event.order > max(1, len(diagnosis.events) // 2)
            and event.narrative_role == "resolution"
        }
        opening_ids = {
            event_id for shot in plan.shots[:2] for event_id in shot.event_ids
        }
        opening_no_spoiler = not bool(late_sensitive & opening_ids)
    if plan.creative_profile == SHORT_DRAMA_PROFILE:
        dramaturgy = plan.dramaturgy
        grounded_quote = (
            _ground_quote(dramaturgy.cold_open_source_quote, episode.source_text)
            if dramaturgy is not None
            else None
        )
        opening_quotes = "".join(shot.source_quote for shot in plan.shots[:2])
        cold_open_grounded = bool(
            grounded_quote
            and _quote_key(grounded_quote) in _quote_key(opening_quotes)
        )
        opening_no_spoiler = cold_open_grounded
        if not cold_open_grounded:
            block(
                "cold_open_not_grounded",
                "短剧冷开场必须直接来自当前章证据，并在前两镜中实际出现",
                shot_indexes=[shot.index for shot in plan.shots[:2]],
            )
    if not opening_no_spoiler and plan.creative_profile != SHORT_DRAMA_PROFILE:
        block("opening_spoils_resolution", "开头直接泄露了章节后半段的答案或结果")
    if not introductions_complete:
        block("character_introductions_missing", "主要人物在承担冲突前没有完成基本建立")
    if future_content_used:
        block("future_content_used", "剧本使用了当前章节之外的新剧情或后文信息")

    moving_shots = [
        shot.index
        for shot in plan.shots
        if shot.camera_plan is not None and shot.camera_plan.mode != "locked"
    ]
    camera_move_ratio = len(moving_shots) / len(plan.shots) if plan.shots else 0.0
    if plan.creative_profile == SHORT_DRAMA_PROFILE:
        adjacent_moves = [
            right
            for left, right in zip(moving_shots, moving_shots[1:])
            if right == left + 1
        ]
        if camera_move_ratio > 0.34:
            block(
                "camera_movement_budget_exceeded",
                f"移动镜头占比{camera_move_ratio:.1%}超过短剧预算；普通对白和反应镜头应保持固定机位",
                shot_indexes=moving_shots,
            )
        if adjacent_moves:
            block(
                "adjacent_camera_moves",
                "相邻镜头不得连续使用明显运镜；运镜必须由揭示、位移或权力变化触发",
                shot_indexes=sorted({index for right in adjacent_moves for index in (right - 1, right)}),
            )
    current_source = _normalized(episode.source_text)
    historical_characters = {
        character.name for character in (previous_state.characters if previous_state else [])
    }
    ungrounded_characters = sorted(
        {
            character
            for shot in plan.shots
            for character in shot.characters
            if _normalized(character) not in current_source
            and character not in historical_characters
        }
    )
    if ungrounded_characters:
        block(
            "future_or_ungrounded_character",
            "分镜使用了当前章未出现且不在上一集状态中的人物："
            + "、".join(ungrounded_characters),
        )

    blocking = [issue for issue in issues if issue.severity == "blocking"]
    return ScriptQualityReport(
        policy_revision=SCRIPT_POLICY_REVISION,
        passed=not blocking,
        script_char_count=script_chars,
        shot_count=len(plan.shots),
        turn_count=len(turns),
        critical_event_coverage=round(coverage, 6),
        causal_chain_complete=causal_complete,
        character_introductions_complete=introductions_complete,
        opening_no_spoiler=opening_no_spoiler,
        ending_at_chapter_boundary=ending_at_boundary,
        future_content_used=future_content_used,
        max_turn_char_count=max(turn_lengths, default=0),
        target_overflow_turn_count=sum(
            length > SHORT_DRAMA_TURN_TARGET_MAX for length in turn_lengths
        ),
        hard_overflow_turn_count=sum(
            length > SHORT_DRAMA_TURN_HARD_MAX for length in turn_lengths
        ),
        narration_char_count=narration_chars,
        narration_ratio=round(narration_ratio, 6),
        narration_budget_ratio=round(narration_budget, 6),
        cold_open_grounded=cold_open_grounded,
        camera_move_ratio=round(camera_move_ratio, 6),
        issues=issues,
    )


def deterministic_series_state(
    episode: Episode,
    diagnosis: ChapterDiagnosis,
    previous_state: SeriesState | None,
) -> SeriesState:
    event = diagnosis.events[-1]
    evidence = GroundedStateFact(
        statement=diagnosis.chapter_end_state,
        source_episode=episode.index,
        source_quote=event.source_quote,
    )
    prior_characters = previous_state.characters if previous_state else []
    return SeriesState(
        current_episode=episode.index,
        timeline=[*(previous_state.timeline if previous_state else []), evidence],
        characters=prior_characters,
        relationships=previous_state.relationships if previous_state else [],
        props=previous_state.props if previous_state else [],
        open_loops=previous_state.open_loops if previous_state else [],
        resolved_loops=previous_state.resolved_loops if previous_state else [],
        potential_foreshadowing=previous_state.potential_foreshadowing if previous_state else [],
        previous_episode_end=EpisodeEndState(
            location="当前章节最后场景",
            action=diagnosis.chapter_end_state,
            final_visual=diagnosis.chapter_end_state,
            evidence=evidence,
        ),
    )


def _state_facts(state: SeriesState | None) -> set[tuple[int, str, str]]:
    if state is None:
        return set()
    facts: list[GroundedStateFact] = [*state.timeline, *state.potential_foreshadowing]
    for character in state.characters:
        facts.append(character.evidence)
        facts.extend(character.known_information)
    facts.extend(item.evidence for item in state.relationships)
    facts.extend(item.evidence for item in state.props)
    facts.extend(item.evidence for item in state.open_loops)
    facts.extend(item.evidence for item in state.resolved_loops)
    if state.previous_episode_end:
        facts.append(state.previous_episode_end.evidence)
    return {(fact.source_episode, fact.statement, fact.source_quote) for fact in facts}


def validate_series_state(
    state: SeriesState,
    episode: Episode,
    previous_state: SeriesState | None,
) -> SeriesState:
    if state.current_episode != episode.index:
        raise ValueError("series state current_episode must equal the current chapter index")
    source = _normalized(episode.source_text)
    previous = _state_facts(previous_state)
    current_facts = _state_facts(state)
    issues = []
    for source_episode, statement, quote in current_facts:
        if source_episode == episode.index:
            if _normalized(quote) not in source:
                issues.append(f"new state fact is not grounded in current chapter: {statement}")
        elif (source_episode, statement, quote) not in previous:
            issues.append(f"historical state fact was not carried from previous state: {statement}")
    if issues:
        raise ValueError("; ".join(issues))
    return state
