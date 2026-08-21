from __future__ import annotations

import math
import os
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
    TurnDelivery,
    TurnDerivation,
)
from .creative_direction import SHORT_DRAMA_PROFILE


SCRIPT_POLICY_REVISION = "novel-manga-script-v6-showrunner"
# A turn is a complete TTS breath/meaning group, not one subtitle page.  The
# renderer paginates and times subtitles independently after audio alignment.
SHORT_DRAMA_TURN_TARGET_MAX = 36
SHORT_DRAMA_TURN_HARD_MAX = 60
# Showrunner state deltas are only worth gating on once most of the chapter's
# declared changes carry evidence; below this the state layer is decorative.
CHARACTER_DELTA_GROUNDING_FLOOR = 0.6

# Two bars, not one.  The correctness checks below -- invented lines, dialogue
# summarised into narration, a narrator speaking a character's words -- stay on
# in every mode, because failing them produces an episode the audience cannot
# follow.  The craft bars (length, narration share, evidence coverage) are what
# separates a serviceable machine draft from a hand-written one, and a project
# may reasonably choose to ship the former.
def _relaxed() -> bool:
    return os.getenv("NOVEL_SCRIPT_STRICTNESS", "strict").strip().lower() == "relaxed"


def _delta_floor() -> float:
    return 0.15 if _relaxed() else CHARACTER_DELTA_GROUNDING_FLOOR


def _narration_tolerance() -> float:
    return 0.20 if _relaxed() else 0.02


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _quote_key(value: str) -> str:
    """Normalize formatting-only differences without accepting paraphrases."""

    return re.sub(r"[\s，。！？；：、,.!?;:'\"“”‘’（）()《》〈〉…—·-]+", "", value)


# Straight ASCII quotes are as common as typographic ones in scraped chapters,
# and a regex that only knew about “」 silently reported zero dialogue on such a
# text, blinding every speech-attribution check below.
_SPOKEN_LINE = re.compile(r"[“「\"]([^”」\"]+)[”」\"]")
# Third-person narration does not address anyone and does not speak as anyone.
# A narrator turn carrying 我/你 is a character line that lost its speaker,
# which is how quoted dialogue ends up read in the narrator's voice.
_ADDRESSED_SPEECH = re.compile(r"[我你您]")
# Bracketed annotations belong in delivery_mode and performance_plan, never in
# the text a voice actor speaks.
_STAGE_DIRECTION = re.compile(r"[（(【\[][^）)】\]]{0,8}[）)】\]]")


def _spoken_lines(source_quote: str) -> list[str]:
    """Chapter text the author already marked as an uttered line."""

    return [match.group(1) for match in _SPOKEN_LINE.finditer(source_quote)]


def _narration_body(source_quote: str) -> str:
    """The part of a citation that is authorial narration rather than speech."""

    return _SPOKEN_LINE.sub("", source_quote)


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
        # These floors used to assume the only speakable material was the text
        # the author had already put in quotation marks, which in a typical
        # chapter is barely a fifth of it.  Now that narrated passages can be
        # staged as derived dialogue, an episode that stays near the old floor
        # is skipping the adaptation rather than economising.
        if source_chars <= 200:
            return ScriptPolicy(max(8, round(source_chars * 0.25)), 1, 1)
        if source_chars <= 1200:
            turns = max(6, min(10, math.ceil(source_chars / 140)))
            return ScriptPolicy(min(400, max(120, round(source_chars * 0.24))), turns, max(5, turns - 1))
        if source_chars <= 3000:
            return ScriptPolicy(min(900, max(480, round(source_chars * 0.22))), 16, 14)
        return ScriptPolicy(min(1200, max(700, round(source_chars * 0.20))), 20, 16)
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


def repair_machine_draft(plan: EpisodePlan, episode: Episode) -> EpisodePlan:
    """Fix the mechanical mislabels a planner reliably makes, before gating.

    A model that is otherwise writing a usable draft still gets three things
    wrong in a way no amount of prompting has fixed: it marks a line it copied
    out of quotation marks as ``derived``, it marks a line it invented as
    ``verbatim``, and it drops a paragraph of third-person prose into a
    character's inner voice.  All three are decidable from the chapter text, so
    the controller corrects them rather than spending revision rounds on them.
    Anything not decidable here still fails the gates.
    """

    source_key = _quote_key(episode.source_text)
    spoken_keys = [
        key for key in (_quote_key(line) for line in _spoken_lines(episode.source_text)) if key
    ]
    shots = []
    for shot in plan.shots:
        turns = []
        for turn in shot.turns:
            text_key = _quote_key(turn.text)
            update: dict[str, object] = {}

            # A two-character interjection is contained in half the chapter by
            # accident; only treat a line as copied when there is enough of it
            # to be sure.
            quoted = len(text_key) >= 5 and any(
                text_key in line or line in text_key for line in spoken_keys
            )
            if turn.role != "narrator":
                if quoted and turn.derivation != TurnDerivation.VERBATIM:
                    # Copied out of the chapter's quotation marks: that is a
                    # verbatim line however the model labelled it.  Re-anchor
                    # the citation too, or the relabel just trades one gate
                    # failure for another.
                    update["derivation"] = TurnDerivation.VERBATIM
                    if text_key not in _quote_key(turn.source_quote or ""):
                        row = _ground_quote(turn.text, episode.source_text)
                        if row:
                            update["source_quote"] = row[:500]
                elif not quoted and turn.derivation == TurnDerivation.VERBATIM and (
                    text_key not in _quote_key(turn.source_quote or "")
                ):
                    update["derivation"] = TurnDerivation.DERIVED

                speaker_key = _quote_key(turn.speaker_name)
                lifted = len(text_key) >= 25 and text_key in source_key
                # A speaker naming themselves is third-person prose whatever
                # its length; the 25-character floor only ever applied to the
                # verbatim-lift test beside it.
                if not quoted and (
                    (speaker_key and speaker_key in text_key) or lifted
                ):
                    # Third-person prose wearing a character's voice: hand it
                    # back to the narrator, where it is at least honest.
                    update.update(
                        role="narrator",
                        speaker_name="旁白",
                        speaking=False,
                        delivery_mode=TurnDelivery.NARRATION,
                        derivation=TurnDerivation.DERIVED,
                    )
            turns.append(turn.model_copy(update=update) if update else turn)
        shots.append(shot.model_copy(update={"turns": turns}))
    return plan.model_copy(update={"shots": shots})


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
            # Naming the allowed cast inline: without it the repair loop only
            # learns which name was rejected, not which ones it may use, and a
            # crowd noun keeps coming back under a new spelling.
            issues.append(
                f"{event.event_id} uses unknown characters: {sorted(unknown)}; "
                f"characters may only contain StoryBible names: {sorted(known_characters)}; "
                "unnamed crowds belong in description, not in characters"
            )
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

    # A turn may only reach the screen two ways: it quotes a line the chapter
    # already puts in quotation marks, or it stages a narrated passage as
    # performance.  Anything else is either a misattributed line or an invented
    # one, and both read as incoherence once the narration budget trims what is
    # left.
    misattributed: list[int] = []
    ungrounded_verbatim: list[int] = []
    paraphrased_dialogue: list[int] = []
    unrewritten_derived: list[int] = []
    third_person_self: list[int] = []
    stage_directions: list[int] = []
    source_key = _quote_key(episode.source_text)
    for shot in plan.shots:
        for turn in shot.turns:
            quote = turn.source_quote or ""
            text_key = _quote_key(turn.text)
            # turn.text is spoken verbatim by TTS and burned into subtitles, so
            # a bracketed stage direction such as （内心） is read out loud.
            # delivery_mode already carries that information.
            if _STAGE_DIRECTION.search(turn.text):
                stage_directions.append(shot.index)
                continue
            if turn.role != "narrator" and turn.derivation == TurnDerivation.DERIVED:
                # Relabelling narration as a character's inner voice is not
                # adaptation: the model keeps the third-person prose and only
                # changes the delivery field, so the character ends up narrating
                # themselves by name.
                speaker_key = _quote_key(turn.speaker_name)
                if speaker_key and speaker_key in text_key:
                    third_person_self.append(shot.index)
                    continue
                # A staged line may reuse a short factual phrase word for word
                # ("四岁练气，十岁拥有九段战之气") and still be genuine speech;
                # what is not adaptation is lifting a whole clause of
                # third-person prose unchanged.  Hand-written baselines top out
                # around 13 characters of verbatim reuse, relabelled narration
                # starts around 28.  Scan windows rather than the whole line:
                # prefixing a stage direction made a straight containment test
                # miss the lift it was written to catch.
                if any(
                    text_key[i : i + 25] in source_key
                    for i in range(len(text_key) - 24)
                ):
                    unrewritten_derived.append(shot.index)
                    continue
            if turn.role == "narrator":
                spoken_here = _spoken_lines(quote)
                if any(
                    _quote_key(line) and _quote_key(line) in text_key
                    for line in spoken_here
                ) or _ADDRESSED_SPEECH.search(turn.text):
                    misattributed.append(shot.index)
                    continue
            if not quote:
                # Citations stay optional here; requiring one on every turn is a
                # separate policy change, not part of the grounding contract.
                continue
            if turn.derivation == TurnDerivation.VERBATIM:
                if text_key not in _quote_key(quote):
                    ungrounded_verbatim.append(shot.index)
            elif not _quote_key(_narration_body(quote)):
                paraphrased_dialogue.append(shot.index)
    if misattributed:
        block(
            "narrator_speaks_character_line",
            "旁白turn说了角色的话：它引用了原文引号内的台词，或含有第一/第二人称。"
            "带引号的台词和任何带我/你的句子都必须归给具体角色，并设置"
            "visible_dialogue、offscreen_dialogue或inner_voice",
            shot_indexes=sorted(set(misattributed)),
        )
    if ungrounded_verbatim:
        block(
            "verbatim_turn_not_quoted",
            "derivation=verbatim的turn文本必须逐字出现在其source_quote中；"
            "如果这句话是由叙述改写而来，请设置derivation=derived并引用对应叙述句",
            shot_indexes=sorted(set(ungrounded_verbatim)),
        )
    if stage_directions:
        block(
            "turn_text_contains_stage_direction",
            "turn.text会被逐字配音并烧进字幕，其中不得出现（内心）这类括号提示；"
            "该信息用delivery_mode表示，表演细节写进performance_plan",
            shot_indexes=sorted(set(stage_directions)),
        )
    if third_person_self:
        block(
            "derived_turn_narrates_self",
            "角色台词里出现了说话人自己的名字，说明这是把第三人称叙述直接搬进了台词或内心声。"
            "角色说自己的话要用第一人称，叙述内容必须真正改写成他会说出口的话",
            shot_indexes=sorted(set(third_person_self)),
        )
    if unrewritten_derived:
        block(
            "derived_turn_not_rewritten",
            "derivation=derived的角色台词与原文叙述逐字相同，等于只换了标签没有改写。"
            "把叙述改写成角色真会说出口的口语，或拆成有听者的一问一答",
            shot_indexes=sorted(set(unrewritten_derived)),
        )
    if paraphrased_dialogue:
        block(
            "derived_turn_paraphrases_dialogue",
            "derivation=derived的turn必须引用含叙述的原文；不得把原文引号内的台词改写成近似句，"
            "台词要么逐字引用，要么由叙述外化",
            shot_indexes=sorted(set(paraphrased_dialogue)),
        )

    # A narrator turn may cite a passage that happens to contain speech, as long
    # as that speech is actually delivered somewhere in the episode.  What is
    # not allowed is citing quoted dialogue and summarising it away -- five
    # separate taunts collapsing into "众人纷纷嘲讽他" is the single fastest way
    # to turn an adaptation back into a plot summary, and no existing check
    # caught it because the narrator text does not contain the quote.
    # Speech is recognised from the chapter itself rather than from the
    # citation: a turn-level source_quote routinely arrives with its outer
    # quotation marks stripped, so scanning the citation alone finds nothing.
    source_spoken = [
        key for key in (_quote_key(line) for line in _spoken_lines(episode.source_text)) if key
    ]
    spoken_delivered = {
        _quote_key(turn.text)
        for shot in plan.shots
        for turn in shot.turns
        if turn.role != "narrator" and _quote_key(turn.text)
    }

    def _is_delivered(line_key: str) -> bool:
        return any(
            line_key in delivered or delivered in line_key
            for delivered in spoken_delivered
        )

    summarised_dialogue = sorted(
        {
            shot.index
            for shot in plan.shots
            for turn in shot.turns
            if turn.role == "narrator" and _quote_key(turn.source_quote or "")
            for line_key in source_spoken
            if (
                line_key in _quote_key(turn.source_quote)
                or _quote_key(turn.source_quote) in line_key
            )
            and not _is_delivered(line_key)
        }
    )
    if summarised_dialogue:
        block(
            "narrator_summarises_dialogue",
            "旁白引用了原文引号内的台词，但这些台词没有被任何角色说出来，"
            "等于把对白概括成了旁白。原文有几句台词就让角色说几句，"
            "旁白只保留无法表演的时间、空间和必要规则",
            shot_indexes=summarised_dialogue,
        )

    verbatim_turn_count = sum(
        1 for turn in turns if turn.derivation == TurnDerivation.VERBATIM
    )
    derived_turn_count = len(turns) - verbatim_turn_count
    derived_chars = sum(
        len(_normalized(turn.text))
        for turn in turns
        if turn.derivation == TurnDerivation.DERIVED
    )
    derived_char_ratio = derived_chars / script_chars if script_chars else 0.0

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
        and narration_ratio > narration_budget + _narration_tolerance()
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

    retention_beat_coverage = 0.0
    max_attention_gap_ratio = 1.0
    information_fact_grounding = 0.0
    character_delta_grounding = 0.0
    shot_intent_coverage = 0.0
    audio_beat_coverage = 0.0
    if plan.creative_profile == SHORT_DRAMA_PROFILE:
        showrunner = plan.showrunner_plan
        if showrunner is None:
            block(
                "showrunner_plan_missing",
                "短剧模式必须先生成留存、信息差和人物状态决策，再进入分镜执行",
            )
        else:
            event_ids = set(events)
            shot_indexes = {shot.index for shot in plan.shots}
            fact_ids = {fact.fact_id for fact in showrunner.information_states}
            beat_ids = {beat.beat_id for beat in showrunner.retention.beats}
            valid_beats = 0
            for beat in showrunner.retention.beats:
                grounded = _ground_quote(beat.source_quote, episode.source_text) is not None
                known_events = bool(beat.event_ids) and set(beat.event_ids) <= event_ids
                known_shots = bool(beat.shot_indexes) and set(beat.shot_indexes) <= shot_indexes
                known_facts = set(beat.new_information_fact_ids) <= fact_ids
                if grounded and known_events and known_shots and known_facts:
                    valid_beats += 1
                else:
                    block(
                        "retention_beat_not_grounded",
                        f"留存节点{beat.beat_id}必须引用当前章事件、有效镜头和逐字原文证据",
                        shot_indexes=[index for index in beat.shot_indexes if index in shot_indexes],
                        event_ids=[event_id for event_id in beat.event_ids if event_id in event_ids],
                    )
            retention_beat_coverage = (
                valid_beats / len(showrunner.retention.beats)
                if showrunner.retention.beats
                else 0.0
            )
            starts = [beat.target_start_ratio for beat in showrunner.retention.beats]
            points = [0.0, *starts, 1.0]
            max_attention_gap_ratio = max(
                (right - left for left, right in zip(points, points[1:])),
                default=1.0,
            )
            if max_attention_gap_ratio > showrunner.retention.max_attention_gap_ratio + 0.01:
                block(
                    "attention_gap_too_large",
                    f"相邻留存节点最大间隔{max_attention_gap_ratio:.1%}超过预算"
                    f"{showrunner.retention.max_attention_gap_ratio:.1%}；中段存在注意力空窗",
                )
            functions = {beat.function for beat in showrunner.retention.beats}
            if not {"hook", "question", "cliffhanger"} <= functions:
                block(
                    "retention_functions_missing",
                    "留存计划至少需要hook、question和cliffhanger节点",
                )
            if not functions & {"payoff", "reversal"}:
                block(
                    "payoff_missing",
                    "留存计划必须包含至少一次有原文依据的payoff或reversal",
                )
            hook_beats = [beat for beat in showrunner.retention.beats if beat.function == "hook"]
            if not hook_beats or not any(
                beat.target_start_ratio <= 0.05
                and set(beat.shot_indexes) & {shot.index for shot in plan.shots[:2]}
                for beat in hook_beats
            ):
                block(
                    "retention_hook_misaligned",
                    "hook必须落在前5%并映射前两镜",
                    shot_indexes=[shot.index for shot in plan.shots[:2]],
                )
            cliff_beats = [
                beat for beat in showrunner.retention.beats if beat.function == "cliffhanger"
            ]
            if not cliff_beats or not any(
                beat.target_start_ratio >= 0.8
                and plan.shots[-1].index in beat.shot_indexes
                for beat in cliff_beats
            ):
                block(
                    "retention_cliffhanger_misaligned",
                    "cliffhanger必须落在后20%并映射最后一镜",
                    shot_indexes=[plan.shots[-1].index],
                )

            valid_facts = 0
            for fact in showrunner.information_states:
                if (
                    _ground_quote(fact.source_quote, episode.source_text) is not None
                    and set(fact.source_event_ids) <= event_ids
                    and (not fact.reveal_beat_id or fact.reveal_beat_id in beat_ids)
                ):
                    valid_facts += 1
                else:
                    block(
                        "information_fact_not_grounded",
                        f"信息状态{fact.fact_id}必须绑定当前章事实、事件和揭示节点",
                        event_ids=[
                            event_id
                            for event_id in fact.source_event_ids
                            if event_id in event_ids
                        ],
                    )
            information_fact_grounding = (
                valid_facts / len(showrunner.information_states)
                if showrunner.information_states
                else 0.0
            )
            if not showrunner.information_states:
                block(
                    "information_graph_empty",
                    "短剧模式必须显式记录至少一个观众与角色的信息状态",
                )

            valid_deltas = 0
            for delta in showrunner.character_state_deltas:
                changed = delta.before != delta.after
                grounded = _ground_quote(delta.source_quote, episode.source_text) is not None
                known_events = set(delta.event_ids) <= event_ids
                if changed and grounded and known_events:
                    valid_deltas += 1
                else:
                    block(
                        "character_state_delta_not_grounded",
                        f"人物{delta.character_name}的状态变化必须有前后差异和当前章证据",
                        event_ids=[
                            event_id for event_id in delta.event_ids if event_id in event_ids
                        ],
                    )
            expected_delta_events = {
                event.event_id
                for event in diagnosis.events
                if event.state_change and event.characters
            }
            covered_delta_events = {
                event_id
                for delta in showrunner.character_state_deltas
                for event_id in delta.event_ids
            }
            missing_delta_events = sorted(expected_delta_events - covered_delta_events)
            if missing_delta_events:
                block(
                    "character_state_delta_missing",
                    "章节诊断已声明人物状态变化，但Showrunner未记录状态增量",
                    event_ids=missing_delta_events,
                )
            delta_denominator = max(
                len(showrunner.character_state_deltas), len(expected_delta_events)
            )
            character_delta_grounding = (
                valid_deltas / delta_denominator if delta_denominator else 1.0
            )
            # The metric was computed and reported but never compared against
            # anything, so an episode could claim a Showrunner state layer while
            # leaving most of the chapter's declared changes unrecorded.
            if character_delta_grounding < _delta_floor():
                block(
                    "character_delta_grounding_low",
                    f"人物状态增量证据覆盖率{character_delta_grounding:.1%}低于下限"
                    f"{_delta_floor():.0%}；章节声明发生变化的人物必须记录"
                    "带当前章证据的before/after增量",
                )

            intended_shots = [
                shot
                for shot in plan.shots
                if shot.shot_intent.retention_beat_id in beat_ids
                and set(shot.shot_intent.information_fact_ids) <= fact_ids
            ]
            shot_intent_coverage = len(intended_shots) / len(plan.shots) if plan.shots else 0.0
            if len(intended_shots) != len(plan.shots):
                block(
                    "shot_intent_incomplete",
                    "每个镜头必须绑定一个有效留存节点，信息镜头还必须引用有效事实ID",
                    shot_indexes=[
                        shot.index for shot in plan.shots if shot not in intended_shots
                    ],
                )

            directed_audio_shots = [
                shot
                for shot in plan.shots
                if shot.audio_plan.audio_beats
                and all(
                    beat.retention_beat_id in beat_ids
                    for beat in shot.audio_plan.audio_beats
                )
            ]
            audio_beat_coverage = (
                len(directed_audio_shots) / len(plan.shots) if plan.shots else 0.0
            )
            if len(directed_audio_shots) != len(plan.shots):
                block(
                    "audio_beat_plan_incomplete",
                    "每个短剧镜头必须有按触发执行的相对音频节拍",
                    shot_indexes=[
                        shot.index for shot in plan.shots if shot not in directed_audio_shots
                    ],
                )
            if showrunner.planning_mode == "inferred_fallback":
                issues.append(
                    ScriptReviewIssue(
                        code="showrunner_inferred_fallback",
                        severity="warning",
                        message="当前Showrunner计划由确定性回退生成；可生产但应优先由规划模型给出信息差与人物状态决策",
                    )
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
        retention_beat_coverage=round(retention_beat_coverage, 6),
        max_attention_gap_ratio=round(max_attention_gap_ratio, 6),
        information_fact_grounding=round(information_fact_grounding, 6),
        character_delta_grounding=round(character_delta_grounding, 6),
        character_delta_grounding_floor=_delta_floor(),
        shot_intent_coverage=round(shot_intent_coverage, 6),
        audio_beat_coverage=round(audio_beat_coverage, 6),
        verbatim_turn_count=verbatim_turn_count,
        derived_turn_count=derived_turn_count,
        derived_char_ratio=round(derived_char_ratio, 6),
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
        information_states=(previous_state.information_states if previous_state else []),
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
    facts.extend(item.evidence for item in state.information_states)
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
