from __future__ import annotations

import hashlib
import json
import random
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
from typing import TypeVar

import httpx
from pydantic import ValidationError

from .config import NATIVE_DIALOGUE_POLICY, Settings
from .creative_direction import (
    SHORT_DRAMA_PROFILE,
    apply_creative_direction,
    creative_prompt_brief,
)
from .models import (
    AdaptationLedgerItem,
    CameraBeat,
    CameraPlan,
    BeatScriptShot,
    Character,
    ChapterDiagnosis,
    Episode,
    EpisodeDramaturgy,
    EpisodeMode,
    EpisodeOpposition,
    EpisodeContract,
    EpisodePlanningBundle,
    EpisodePlan,
    MotionBeat,
    NovelDocument,
    PerformancePlan,
    RetentionBeat,
    RetentionBeatDirection,
    RetentionBeatScript,
    ScriptTurn,
    SpeechStrategy,
    ScriptQualityReport,
    ScriptContentPatch,
    ScriptExpansion,
    SeriesState,
    SeriesDevelopmentPlan,
    SeriesDevelopmentReview,
    ShowrunnerPlan,
    Shot,
    StoryBible,
    TurnDelivery,
    TurnDerivation,
    TurnDevice,
)
from .safety import safe_visual_prompt
from .script_planning import (
    SHORT_DRAMA_TURN_HARD_MAX,
    _narration_body,
    _quote_key,
    _spoken_lines,
    abridged_clause_subsequence,
    bind_deterministic_events,
    deterministic_chapter_diagnosis,
    deterministic_series_state,
    effective_script_policy,
    evaluate_script_quality,
    repair_machine_draft,
    restore_abridged_clause_punctuation,
    normalize_chronological_plan,
    source_evidence_units,
    validate_chapter_diagnosis,
    validate_series_state,
)
from .util import atomic_write_json


STYLE = (
    "精致国漫动态漫画，二维赛璐璐手绘，清晰墨线，柔和电影光影，"
    "人物五官稳定，服饰连续，竖屏中近景构图，禁止真人照片、3D和欧美卡通混入"
)

DIAGNOSIS_TOKEN_BUDGET = 6000
SCRIPT_TOKEN_BUDGET = 32000
REVIEW_TOKEN_BUDGET = 4000
SHOWRUNNER_TOKEN_BUDGET = 6000
SERIES_STATE_TOKEN_BUDGET = 5000
SCRIPT_EXPANSION_TOKEN_BUDGET = 6000
TURN_ATTRIBUTION_TOKEN_BUDGET = 6000
CONTENT_PATCH_TOKEN_BUDGET = 8000
BEAT_SCRIPT_TOKEN_BUDGET = 8000
BEAT_DIRECTION_TOKEN_BUDGET = 7000
SERIES_DEVELOPMENT_TOKEN_BUDGET = 10000
SERIES_DEVELOPMENT_REVIEW_TOKEN_BUDGET = 4000

DIALOGUE_ATTRIBUTION_CODES = {
    "narrator_speaks_character_line",
    "narrator_summarises_dialogue",
}
ATTRIBUTION_REPAIR_CODES = DIALOGUE_ATTRIBUTION_CODES | {
    "narration_budget_exceeded",
}
REVIEW_CONTENT_PATCH_TOKENS = {
    "CAUSAL_CONTEXT",
    "CAUSALITY",
    "MOTIVATION",
}

ValidatedT = TypeVar("ValidatedT")


class EpisodePlanningFailed(ValueError):
    """Terminal episode-planning failure that must never enter media stages."""

    def __init__(
        self,
        message: str,
        *,
        episode_index: int,
        failed_stage: str,
        attempts: int,
        elapsed_seconds: float,
        failed_beat_id: str | None = None,
        intermediate_root: Path | None = None,
    ):
        super().__init__(message)
        self.episode_index = episode_index
        self.failed_stage = failed_stage
        self.attempts = attempts
        self.elapsed_seconds = elapsed_seconds
        self.failed_beat_id = failed_beat_id
        self.intermediate_root = intermediate_root


def _begin_planning_deadline(owner: object, settings: Settings, episode: Episode, root: Path) -> None:
    started = time.monotonic()
    setattr(owner, "_planning_started", started)
    setattr(owner, "_planning_deadline", started + settings.planning_timeout_seconds)
    setattr(owner, "_planning_episode", episode)
    setattr(owner, "_planning_intermediate_root", root)


def _check_planning_deadline(owner: object, stage: str) -> None:
    deadline = getattr(owner, "_planning_deadline", None)
    if deadline is None or time.monotonic() <= deadline:
        return
    episode = getattr(owner, "_planning_episode")
    started = float(getattr(owner, "_planning_started"))
    raise EpisodePlanningFailed(
        f"episode planning exceeded the configured time limit during {stage}",
        episode_index=episode.index,
        failed_stage=stage,
        attempts=0,
        elapsed_seconds=time.monotonic() - started,
        intermediate_root=getattr(owner, "_planning_intermediate_root", None),
    )


def _loads_json_object(value: str) -> dict:
    """Parse model JSON with bounded repairs for punctuation-only defects."""

    match = re.search(r"\{.*\}", value, re.S)
    if not match:
        raise ValueError("LLM did not return a JSON object")
    candidate = match.group(0)
    for _ in range(12):
        try:
            data = json.loads(candidate)
            if not isinstance(data, dict):
                raise ValueError("LLM JSON root must be an object")
            return data
        except json.JSONDecodeError as error:
            if error.msg == "Expecting ',' delimiter":
                previous = candidate[: error.pos].rstrip()
                following = candidate[error.pos :].lstrip()
                if previous and following and previous[-1] in '}\"]0123456789e' and following[0] in '{[\"':
                    candidate = candidate[: error.pos] + "," + candidate[error.pos :]
                    continue
            if error.msg == "Expecting property name enclosed in double quotes":
                previous = candidate[: error.pos].rstrip()
                if previous.endswith(","):
                    comma = candidate.rfind(",", 0, error.pos)
                    candidate = candidate[:comma] + candidate[comma + 1 :]
                    continue
            raise
    raise ValueError("LLM JSON exceeded the bounded punctuation repair budget")


def _validation_feedback(error: ValueError) -> list[dict[str, object]]:
    if isinstance(error, ValidationError):
        return [
            {
                "location": [str(item) for item in row["loc"]],
                "type": row["type"],
                "message": row["msg"],
            }
            for row in error.errors(include_url=False)
        ]
    return [{"type": type(error).__name__, "message": str(error)[:3000]}]


def _validation_retry(
    revision: int,
    data: dict | None,
    error: ValidationError | ValueError,
    previous_retry: dict | None,
) -> dict[str, object]:
    """Build one bounded repair or independent-resample request."""

    # Structural feedback can repair a malformed draft, but repeatedly showing
    # a thin draft to the model anchors later attempts to the same writing.
    # Alternate one repair with one clean sample so every fresh candidate gets
    # a chance to fix mechanical schema errors without monopolising the budget.
    retry: dict[str, object] = {
        "revision": revision + 1,
        "validation_errors": _validation_feedback(error),
    }
    if revision == 0 or (previous_retry and previous_retry.get("resample")):
        retry["previous_response"] = data
    else:
        retry["resample"] = True
    return retry


def _bounded_validate(
    operation: str,
    max_revisions: int,
    request: Callable[[dict | None], dict],
    validate: Callable[[dict], ValidatedT],
) -> ValidatedT:
    """Ask a planner to repair only invalid structured output, with a hard limit."""

    repair: dict | None = None
    last_error: ValueError | None = None
    for revision in range(max_revisions + 1):
        data: dict | None = None
        try:
            data = request(repair)
            return validate(data)
        except (ValidationError, ValueError) as error:
            last_error = error
            if revision >= max_revisions:
                break
            repair = _validation_retry(revision, data, error, repair)
    assert last_error is not None
    details = json.dumps(_validation_feedback(last_error), ensure_ascii=False)
    raise ValueError(
        f"planner operation {operation} remained invalid after "
        f"{max_revisions + 1} attempt(s): {details}"
    ) from last_error


def _validate_story_bible(data: dict, novel: NovelDocument) -> StoryBible:
    bible = StoryBible.model_validate(data)
    issues: list[dict[str, object]] = []
    if re.sub(r"\s+", "", bible.novel_title) != re.sub(r"\s+", "", novel.title):
        issues.append({"field": "novel_title", "message": "must equal the requested novel title"})
    if not bible.characters:
        issues.append({"field": "characters", "message": "at least one reusable character is required"})
    if not bible.locations:
        issues.append({"field": "locations", "message": "at least one reusable location is required"})
    names = [character.name.strip() for character in bible.characters]
    if len(set(names)) != len(names):
        issues.append({"field": "characters", "message": "character names must be unique"})
    for index, character in enumerate(bible.characters):
        if not character.name.strip() or not character.appearance.strip() or not character.wardrobe.strip():
            issues.append({
                "field": f"characters.{index}",
                "message": "name, appearance, and wardrobe must be non-empty",
            })
    if issues:
        raise ValueError(json.dumps({"domain_errors": issues}, ensure_ascii=False))
    return bible.model_copy(
        update={"style_fingerprint": _fingerprint(novel.title, bible.visual_style, bible.characters)}
    )


def _validate_retention_beat_script(
    data: dict,
    *,
    beat: RetentionBeat,
    episode: Episode,
    bible: StoryBible,
    native_dialogue: bool,
    episode_contract: EpisodeContract | None = None,
    released_fact_ids: list[str] | None = None,
    diagnosis: ChapterDiagnosis | None = None,
) -> RetentionBeatScript:
    data = json.loads(json.dumps(data, ensure_ascii=False))
    data["released_fact_ids"] = list(beat.new_information_fact_ids)
    source_units = source_evidence_units(episode.source_text)
    episode_source_key = _quote_key(episode.source_text)
    canonical_names = {character.name for character in bible.characters}
    diagnosis_events = (
        {event.event_id: event for event in diagnosis.events}
        if diagnosis is not None
        else {}
    )

    def anonymous_name(value: str) -> bool:
        return bool(
            re.search(
                r"(?:人群|群众|群声|族人|少年|少女|姐妹|同伴|过客|路人|弟子|侍者|围观者|画外音|声音)",
                value,
            )
        )

    anonymous_carrier_context = bool(
        re.search(
            r"(?:人群|群众|族人|姐妹|同伴|围观)",
            " ".join(
                str(shot.get("blocking", ""))
                + str(shot.get("change", ""))
                for shot in data.get("shots", [])
            ),
        )
    )
    for raw_shot in data.get("shots", []):
        shot_quote_key = _quote_key(
            str(raw_shot.get("source_quote", ""))
        )
        if not shot_quote_key or shot_quote_key not in episode_source_key:
            grounded_event_quote = next(
                (
                    diagnosis_events[event_id].source_quote
                    for event_id in raw_shot.get("event_ids", [])
                    if event_id in diagnosis_events
                    and _quote_key(diagnosis_events[event_id].source_quote)
                    in episode_source_key
                ),
                None,
            )
            if grounded_event_quote is not None:
                raw_shot["source_quote"] = grounded_event_quote
        raw_turns = list(raw_shot.get("turns", []))
        redundant_sound_turns = {
            index
            for index, turn in enumerate(raw_turns)
            if turn.get("derivation") == TurnDerivation.DERIVED
            and turn.get("delivery_mode")
            == TurnDelivery.OFFSCREEN_DIALOGUE
            and re.fullmatch(
                r"[（(][^）)]*(?:狼嚎|骚动|呼喊|叫喊|声)[^）)]*[）)]",
                str(turn.get("text", "")).strip(),
            )
            and any(
                other_index != index
                and other.get("delivery_mode")
                == TurnDelivery.SILENT_ACTION
                and set(turn.get("serves", []))
                <= set(other.get("serves", []))
                for other_index, other in enumerate(raw_turns)
            )
        }
        if redundant_sound_turns:
            raw_shot["turns"] = [
                turn
                for index, turn in enumerate(raw_turns)
                if index not in redundant_sound_turns
            ]
        raw_intent = raw_shot.get("shot_intent")
        if (
            isinstance(raw_intent, dict)
            and raw_intent.get("dramatic_function") == "turning_point"
        ):
            raw_intent["dramatic_function"] = "reveal"
        for raw_turn in raw_shot.get("turns", []):
            if (
                native_dialogue
                and anonymous_carrier_context
                and (
                    raw_turn.get("delivery_mode") == TurnDelivery.NARRATION
                    or raw_turn.get("device") == TurnDevice.NARRATION
                )
            ):
                raw_turn.update(
                    {
                        "role": "无名族人",
                        "speaker_name": "无名族人",
                        "speaking": False,
                        "delivery_mode": TurnDelivery.OFFSCREEN_DIALOGUE,
                        "derivation": TurnDerivation.DERIVED,
                        "device": TurnDevice.CROWD_PROXY,
                        "serves": list(raw_shot.get("event_ids", [])),
                    }
                )
            speaker = str(raw_turn.get("speaker_name", "")).strip()
            if (
                native_dialogue
                and raw_turn.get("device") == TurnDevice.INNER_VOICE
                and raw_turn.get("delivery_mode")
                in {
                    TurnDelivery.VISIBLE_DIALOGUE,
                    TurnDelivery.OFFSCREEN_DIALOGUE,
                }
            ):
                raw_turn["device"] = TurnDevice.LISTENER_QA
            if (
                raw_turn.get("device") == TurnDevice.CROWD_PROXY
                and raw_turn.get("delivery_mode")
                == TurnDelivery.OFFSCREEN_DIALOGUE
                and not speaker
            ):
                speaker = "无名族人"
                raw_turn["speaker_name"] = speaker
                raw_turn["role"] = speaker
            if (
                raw_turn.get("derivation") == TurnDerivation.DERIVED
                and raw_turn.get("delivery_mode")
                == TurnDelivery.OFFSCREEN_DIALOGUE
                and speaker
                and anonymous_name(speaker)
                and not raw_turn.get("device")
            ):
                raw_turn["device"] = TurnDevice.CROWD_PROXY
            if (
                raw_turn.get("delivery_mode")
                == TurnDelivery.VISIBLE_DIALOGUE
                and not speaker
            ):
                visible_surface = " ".join(
                    (
                        str(raw_shot.get("blocking", "")),
                        str(raw_shot.get("change", "")),
                    )
                )
                speaking_candidates = [
                    name
                    for name in canonical_names
                    if re.search(
                        re.escape(name)
                        + r"[^。；，]{0,24}(?:说|道|喊|开口|宣读|宣布|问|答|叫|低声|高声)",
                        visible_surface,
                    )
                ]
                visible_candidates = [
                    name
                    for name in canonical_names
                    if name in visible_surface
                ]
                if len(speaking_candidates) == 1:
                    speaker = speaking_candidates[0]
                    raw_turn["speaker_name"] = speaker
                    raw_turn["role"] = speaker
                elif len(visible_candidates) == 1:
                    speaker = visible_candidates[0]
                    raw_turn["speaker_name"] = speaker
                    raw_turn["role"] = speaker
            if (
                raw_turn.get("delivery_mode")
                == TurnDelivery.VISIBLE_DIALOGUE
                and speaker not in canonical_names
                and anonymous_name(speaker)
            ):
                raw_turn["delivery_mode"] = TurnDelivery.OFFSCREEN_DIALOGUE
                raw_turn["speaking"] = False
            if (
                raw_turn.get("delivery_mode")
                == TurnDelivery.SILENT_ACTION
                and not str(raw_turn.get("text", "")).strip()
            ):
                raw_turn.update(
                    {
                        "role": "action",
                        "speaker_name": "",
                        "text": f"（{raw_shot.get('blocking', '')}）",
                        "speaking": False,
                        "source_quote": raw_turn.get("source_quote")
                        or raw_shot.get("source_quote", ""),
                        "derivation": TurnDerivation.DERIVED,
                        "device": TurnDevice.SPATIAL,
                        "serves": raw_turn.get("serves")
                        or list(raw_shot.get("event_ids", [])),
                    }
                )
            if raw_turn.get("derivation") in {
                TurnDerivation.VERBATIM,
                TurnDerivation.ABRIDGED,
                "verbatim",
                "abridged",
            }:
                text_key = _quote_key(str(raw_turn.get("text", "")))
                quote_key = _quote_key(
                    str(raw_turn.get("source_quote", ""))
                )
                if text_key and text_key not in quote_key:
                    matching_source = next(
                        (
                            source
                            for source in source_units
                            if text_key in _quote_key(source)
                        ),
                        None,
                    )
                    if matching_source is not None:
                        raw_turn["source_quote"] = matching_source
                        raw_turn["derivation"] = TurnDerivation.VERBATIM
            if (
                native_dialogue
                and raw_turn.get("delivery_mode")
                == TurnDelivery.INNER_VOICE
            ):
                raw_turn.update(
                    {
                        "role": "action",
                        "speaker_name": "",
                        "text": f"（{raw_shot.get('blocking', '')}）",
                        "speaking": False,
                        "delivery_mode": TurnDelivery.SILENT_ACTION,
                        "source_quote": raw_shot.get("source_quote", ""),
                        "derivation": TurnDerivation.DERIVED,
                        "device": TurnDevice.SPATIAL,
                        "serves": list(raw_shot.get("event_ids", [])),
                    }
                )
            if (
                raw_turn.get("delivery_mode")
                == TurnDelivery.SILENT_ACTION
                and _quote_key(str(raw_turn.get("source_quote", "")))
                not in episode_source_key
            ):
                raw_turn["source_quote"] = raw_shot.get(
                    "source_quote",
                    "",
                )
            if (
                raw_turn.get("derivation") == TurnDerivation.ABRIDGED
                and not _spoken_lines(
                    str(raw_turn.get("source_quote", ""))
                )
                and not abridged_clause_subsequence(
                    [str(raw_turn.get("text", ""))],
                    str(raw_turn.get("source_quote", "")),
                )
            ):
                raw_turn["derivation"] = TurnDerivation.DERIVED
                raw_turn["device"] = (
                    TurnDevice.CROWD_PROXY
                    if anonymous_name(speaker)
                    else TurnDevice.LISTENER_QA
                )
                raw_turn["serves"] = list(
                    raw_shot.get("event_ids", [])
                )
            if (
                raw_turn.get("derivation") == TurnDerivation.ABRIDGED
                and len(_spoken_lines(str(raw_turn.get("source_quote", "")))) != 1
                and raw_turn.get("device")
                and raw_turn.get("serves")
            ):
                raw_turn["derivation"] = TurnDerivation.DERIVED
    script = RetentionBeatScript.model_validate(data)
    beat_event_ids = set(beat.event_ids)
    normalized_shots = []
    for shot in script.shots:
        turn_event_matches = {}
        for turn_index, turn in enumerate(shot.turns):
            turn_key = _quote_key(turn.source_quote)
            turn_event_matches[turn_index] = {
                event_id
                for event_id, event in diagnosis_events.items()
                if turn_key
                and (
                    turn_key in _quote_key(event.source_quote)
                    or _quote_key(event.source_quote) in turn_key
                )
            }
        inferred_inside = {
            event_id
            for matches in turn_event_matches.values()
            for event_id in matches
            if event_id in beat_event_ids
        }
        inside_event_ids = [
            event_id
            for event_id in beat.event_ids
            if event_id in set(shot.event_ids) | inferred_inside
        ]
        if not inside_event_ids:
            continue
        filtered_turns = [
            turn
            for turn_index, turn in enumerate(shot.turns)
            if not turn_event_matches[turn_index]
            or turn_event_matches[turn_index] & beat_event_ids
        ]
        if not filtered_turns:
            continue
        shot = shot.model_copy(
            update={
                "event_ids": inside_event_ids,
                "turns": filtered_turns,
            }
        )
        exact_turns = []
        for turn in shot.turns:
            clean_length = len(re.sub(r"\s+", "", turn.text))
            if (
                turn.delivery_mode
                not in {TurnDelivery.TITLE_CARD, TurnDelivery.SILENT_ACTION}
                and clean_length > SHORT_DRAMA_TURN_HARD_MAX
            ):
                remaining = turn.text
                while remaining:
                    if len(re.sub(r"\s+", "", remaining)) <= SHORT_DRAMA_TURN_HARD_MAX:
                        chunk, remaining = remaining, ""
                    else:
                        raw_end = min(SHORT_DRAMA_TURN_HARD_MAX, len(remaining))
                        preferred = max(
                            (
                                index + 1
                                for index, character in enumerate(
                                    remaining[:raw_end]
                                )
                                if character in "，。！？；：、…,.!?;:"
                            ),
                            default=raw_end,
                        )
                        chunk, remaining = (
                            remaining[:preferred],
                            remaining[preferred:],
                        )
                    exact_turns.append(turn.model_copy(update={"text": chunk}))
            else:
                exact_turns.append(turn)
        shot = shot.model_copy(update={"turns": exact_turns})
        normalized_turns = []
        for turn in shot.turns:
            update = {}
            if turn.delivery_mode == TurnDelivery.SILENT_ACTION:
                update.update(
                    {
                        "derivation": TurnDerivation.DERIVED,
                        "device": turn.device or TurnDevice.SPATIAL,
                        "serves": turn.serves or list(shot.event_ids),
                    }
                )
            if (
                turn.derivation == TurnDerivation.DERIVED
                and turn.delivery_mode != TurnDelivery.SILENT_ACTION
                and not _quote_key(_narration_body(turn.source_quote))
            ):
                served_event = next(
                    (
                        diagnosis_events[value]
                        for value in turn.serves
                        if value in diagnosis_events
                    ),
                    None,
                )
                if served_event is not None:
                    update["source_quote"] = served_event.source_quote
            normalized_turns.append(turn.model_copy(update=update))
        turn_quote_keys = [
            _quote_key(turn.source_quote)
            for turn in normalized_turns
            if _quote_key(turn.source_quote)
        ]
        inferred_events = [
            event_id
            for event_id in beat.event_ids
            if event_id in diagnosis_events
            and any(
                turn_key in _quote_key(diagnosis_events[event_id].source_quote)
                or _quote_key(diagnosis_events[event_id].source_quote)
                in turn_key
                for turn_key in turn_quote_keys
            )
        ]
        served_events = [
            value
            for turn in normalized_turns
            if turn.derivation == TurnDerivation.DERIVED
            for value in turn.serves
            if value.startswith("event_") and value in beat.event_ids
        ]
        normalized_shots.append(
            shot.model_copy(
                update={
                    "event_ids": list(
                        dict.fromkeys(
                            [
                                *shot.event_ids,
                                *inferred_events,
                                *served_events,
                            ]
                        )
                    ),
                    "turns": normalized_turns,
                    "characters": list(
                        dict.fromkeys(
                            [
                                name
                                for name in shot.characters
                                if name in canonical_names
                                or not anonymous_name(name)
                            ]
                            + [
                                name
                                for name in canonical_names
                                if name
                                in " ".join(
                                    [
                                        shot.blocking,
                                        shot.change,
                                        *[
                                            turn.text
                                            for turn in normalized_turns
                                            if turn.delivery_mode
                                            in {
                                                TurnDelivery.VISIBLE_DIALOGUE,
                                                TurnDelivery.SILENT_ACTION,
                                            }
                                        ],
                                    ]
                                )
                            ]
                        )
                    ),
                }
            )
        )
    script = script.model_copy(update={"shots": normalized_shots})
    issues = []
    if script.beat_id != beat.beat_id:
        issues.append("beat_id must equal the requested retention beat")
    event_ids = beat_event_ids
    covered = {event_id for shot in script.shots for event_id in shot.event_ids}
    if not event_ids <= covered:
        issues.append(
            "beat script did not cover requested events: "
            + ",".join(sorted(event_ids - covered))
        )
    allowed_serves_ids = set(beat.event_ids)
    if episode_contract is not None:
        allowed_serves_ids.update(episode_contract.allowed_information_fact_ids)
    allowed_serves_ids.update(released_fact_ids or [])
    source_key = _quote_key(episode.source_text)
    for shot in script.shots:
        if not set(shot.event_ids) <= event_ids:
            issues.append(f"shot {shot.local_index} references events outside this beat")
        if _quote_key(shot.source_quote) not in source_key:
            issues.append(f"shot {shot.local_index} source_quote is not current-chapter text")
        unknown = set(shot.characters) - canonical_names
        if unknown:
            issues.append(
                f"shot {shot.local_index} uses unknown characters: {sorted(unknown)}"
            )
        if shot.shot_intent.retention_beat_id != beat.beat_id:
            issues.append(f"shot {shot.local_index} must bind {beat.beat_id}")
        for turn_index, turn in enumerate(shot.turns, start=1):
            quote = turn.source_quote or shot.source_quote
            if _quote_key(quote) not in source_key:
                issues.append(
                    f"shot {shot.local_index} turn {turn_index} source_quote is not grounded"
                )
            if (
                turn.derivation == TurnDerivation.VERBATIM
                and _quote_key(turn.text) not in _quote_key(quote)
            ):
                issues.append(
                    f"shot {shot.local_index} turn {turn_index} verbatim text is not an exact source substring"
                )
            if (
                turn.derivation == TurnDerivation.DERIVED
                and turn.delivery_mode != TurnDelivery.SILENT_ACTION
                and not _quote_key(_narration_body(quote))
            ):
                issues.append(
                    f"shot {shot.local_index} turn {turn_index} derived speech paraphrases quoted dialogue instead of staging narration"
                )
            if native_dialogue and turn.delivery_mode in {
                TurnDelivery.NARRATION,
                TurnDelivery.INNER_VOICE,
            }:
                issues.append(
                    f"shot {shot.local_index} turn {turn_index} uses forbidden "
                    f"{turn.delivery_mode}"
                )
            if turn.derivation == TurnDerivation.DERIVED and turn.device is None:
                issues.append(
                    f"shot {shot.local_index} turn {turn_index} derived turn lacks device"
                )
            if turn.derivation == TurnDerivation.DERIVED and not turn.serves:
                issues.append(
                    f"shot {shot.local_index} turn {turn_index} derived turn lacks serves"
                )
            if set(turn.serves) - allowed_serves_ids:
                issues.append(
                    f"shot {shot.local_index} turn {turn_index} serves unknown event/fact ids"
                )
            if {
                value for value in turn.serves if value.startswith("event_")
            } - set(shot.event_ids):
                issues.append(
                    f"shot {shot.local_index} turn {turn_index} serves an event not mapped to its shot"
                )
            if (
                turn.delivery_mode != TurnDelivery.SILENT_ACTION
                and turn.speaker_name not in canonical_names
                and turn.role != "narrator"
            ):
                anonymous = anonymous_name(turn.speaker_name)
                if not anonymous:
                    issues.append(
                        f"shot {shot.local_index} turn {turn_index} invents a named character"
                    )
                if turn.speaking:
                    issues.append(
                        f"shot {shot.local_index} turn {turn_index} anonymous carrier must stay offscreen"
                    )
            if (
                turn.delivery_mode
                not in {TurnDelivery.TITLE_CARD, TurnDelivery.SILENT_ACTION}
                and len(re.sub(r"\s+", "", turn.text)) > SHORT_DRAMA_TURN_HARD_MAX
            ):
                issues.append(
                    f"shot {shot.local_index} turn {turn_index} exceeds "
                    f"{SHORT_DRAMA_TURN_HARD_MAX} voiced characters; use abridged whole-clause deletion or split"
                )
            if native_dialogue and turn.device in {
                TurnDevice.NARRATION,
                TurnDevice.INNER_VOICE,
            }:
                issues.append(
                    f"shot {shot.local_index} turn {turn_index} uses forbidden device {turn.device}"
                )
    if set(script.released_fact_ids) != set(beat.new_information_fact_ids):
        issues.append("released_fact_ids must exactly match this beat's newly released facts")
    script = script.model_copy(deep=True)
    invalid_abridged = False
    texts: list[str] = []
    references: list[tuple[int, int]] = []
    quote = ""
    speaker = ""

    def finish_abridged_run() -> None:
        nonlocal texts, references, quote, speaker, invalid_abridged
        if not texts:
            return
        candidate = texts
        if not abridged_clause_subsequence(candidate, quote):
            restored = restore_abridged_clause_punctuation(candidate, quote)
            if restored is not None:
                candidate = restored
        spoken = _spoken_lines(quote)
        split_verbatim = bool(
            len(spoken) == 1
            and re.sub(r"\s+", "", "".join(candidate))
            == re.sub(r"\s+", "", spoken[0])
        )
        if not split_verbatim and not abridged_clause_subsequence(candidate, quote):
            invalid_abridged = True
        else:
            for (shot_index, turn_index), restored_text in zip(
                references,
                candidate,
                strict=True,
            ):
                turn = script.shots[shot_index].turns[turn_index]
                script.shots[shot_index].turns[turn_index] = turn.model_copy(
                    update={
                        "text": restored_text,
                        **(
                            {"derivation": TurnDerivation.VERBATIM}
                            if split_verbatim
                            else {}
                        ),
                    }
                )
        texts, references, quote, speaker = [], [], "", ""

    for shot_index, shot in enumerate(script.shots):
        for turn_index, turn in enumerate(shot.turns):
            if turn.derivation == TurnDerivation.ABRIDGED:
                current_quote = turn.source_quote or shot.source_quote
                if texts and (
                    current_quote != quote or turn.speaker_name != speaker
                ):
                    finish_abridged_run()
                texts.append(turn.text)
                references.append((shot_index, turn_index))
                quote = current_quote
                speaker = turn.speaker_name
            elif texts:
                finish_abridged_run()
    finish_abridged_run()
    for event_id in beat.event_ids:
        event = diagnosis_events.get(event_id)
        if event is None:
            continue
        concrete_tokens = list(
            dict.fromkeys(
                re.findall(
                    r"(?:[一二三四五六七八九十百\d]+岁|"
                    r"[一二三四五六七八九十百\d]+段|"
                    r"战之气旋|百年|最年轻|一夜之间|越来越少)",
                    event.source_quote + event.description,
                )
            )
        )
        if len(concrete_tokens) < 2:
            continue
        carrier_text = "".join(
            turn.text
            for shot in script.shots
            if event_id in shot.event_ids
            for turn in shot.turns
            if turn.delivery_mode != TurnDelivery.SILENT_ACTION
        )
        carried = [
            token for token in concrete_tokens if token in carrier_text
        ]
        if len(carried) < 2:
            issues.append(
                f"{event_id} concrete facts lack a current-timeline carrier: "
                + ",".join(concrete_tokens)
            )
    if invalid_abridged:
        issues.append(
            "abridged turns must concatenate to an ordered whole-clause subsequence"
        )
    if issues:
        raise ValueError(json.dumps({"beat_script_errors": issues}, ensure_ascii=False))
    return script


def _validate_retention_beat_direction(
    data: dict,
    *,
    beat: RetentionBeat,
    script: RetentionBeatScript,
    native_dialogue: bool,
) -> RetentionBeatDirection:
    data = json.loads(json.dumps(data, ensure_ascii=False))

    def provider_safe_performance_text(value: str) -> str:
        value = re.sub(
            r"[^，。；]*(?:指甲[^，。；]*(?:掌心|皮肉|皮肤)|"
            r"(?:掌心|皮肉|皮肤)[^，。；]*指甲)[^，。；]*[，。；]?",
            "双拳持续握紧，指节与手背受力轮廓清楚，",
            value,
        )
        value = value.replace("指甲", "手部").replace("刺入", "持续收紧")
        value = re.sub(
            r"无(?:血腥)?(?:伤口|流血|破皮)[^，。；]*[，。；]?",
            "",
            value,
        )
        value = re.sub(
            r"眉(?:间|头)[^，。；]*(?:微|不可察)[^，。；]*[，。；]?",
            "头部保持低垂，",
            value,
        )
        for source, replacement in (
            ("眉头微锁", "头部保持低垂"),
            ("眼睫微颤", "视线停住"),
            ("喉结滚动", "肩线收紧"),
            ("细微颤动", "保持收紧"),
            ("轻微震颤", "保持收紧"),
            ("微微颤抖", "保持握紧"),
            ("嘴角勾起", "下颌收紧"),
            ("眼神空洞", "视线停住"),
        ):
            value = value.replace(source, replacement)
        return value

    def provider_safe_data(value):
        if isinstance(value, str):
            return provider_safe_performance_text(value)
        if isinstance(value, list):
            return [provider_safe_data(item) for item in value]
        if isinstance(value, dict):
            return {
                key: provider_safe_data(item) for key, item in value.items()
            }
        return value

    source_changes = {
        shot.local_index: shot.change for shot in script.shots
    }
    for raw_shot in data.get("shots", []):
        if native_dialogue:
            raw_audio = raw_shot.setdefault("audio_plan", {})
            raw_audio["speech_strategy"] = SpeechStrategy.NATIVE
            raw_audio["audio_beats"] = []
        raw_camera = raw_shot.get("camera_plan")
        if isinstance(raw_camera, dict) and raw_camera.get("mode") != "locked":
            motivation = str(raw_camera.get("motivation", "")).strip()
            if not motivation or motivation == "人物表演承担画面动态":
                raw_camera["motivation"] = (
                    "服务镜头变化："
                    + source_changes.get(
                        int(raw_shot.get("source_shot_index", 0)),
                        "当前动作与关系变化",
                    )
                )
    data = provider_safe_data(data)
    direction = RetentionBeatDirection.model_validate(data)

    provider_safe_shots = []
    for directed in direction.shots:
        performance = directed.performance_plan
        safe_beats = [
            beat.model_copy(
                update={
                    "action": provider_safe_performance_text(beat.action),
                    "reaction": provider_safe_performance_text(beat.reaction),
                    "expression_transition": provider_safe_performance_text(
                        beat.expression_transition
                    ),
                    "end_state": provider_safe_performance_text(beat.end_state),
                }
            )
            for beat in performance.motion_beats
        ]
        provider_safe_shots.append(
            directed.model_copy(
                update={
                    "visual_prompt": provider_safe_performance_text(
                        directed.visual_prompt
                    ),
                    "motion_prompt": provider_safe_performance_text(
                        directed.motion_prompt
                    ),
                    "performance_plan": performance.model_copy(
                        update={
                            "start_state": provider_safe_performance_text(
                                performance.start_state
                            ),
                            "motion_beats": safe_beats,
                            "end_state": provider_safe_performance_text(
                                performance.end_state
                            ),
                        }
                    ),
                }
            )
        )
    direction = direction.model_copy(update={"shots": provider_safe_shots})
    script_by_index = {
        source_shot.local_index: source_shot
        for source_shot in script.shots
    }
    collapsed_shots = []
    for directed in direction.shots:
        key = (
            directed.source_shot_index,
            directed.turn_start,
            directed.turn_end,
        )
        source_shot = script_by_index.get(directed.source_shot_index)
        covered_turns = (
            source_shot.turns[
                directed.turn_start - 1 : directed.turn_end
            ]
            if source_shot is not None
            else []
        )
        can_replace_preparation = bool(
            collapsed_shots
            and (
                collapsed_shots[-1].source_shot_index,
                collapsed_shots[-1].turn_start,
                collapsed_shots[-1].turn_end,
            )
            == key
            and covered_turns
            and all(
                turn.delivery_mode == TurnDelivery.SILENT_ACTION
                for turn in covered_turns
            )
        )
        if can_replace_preparation:
            collapsed_shots[-1] = directed
        else:
            collapsed_shots.append(directed)
    direction = direction.model_copy(update={"shots": collapsed_shots})
    source_index_ranges = bool(
        len(direction.shots) == len(script.shots)
        and len({row.source_shot_index for row in direction.shots})
        == len(script.shots)
        and all(
            row.turn_start
            == row.turn_end
            == row.source_shot_index
            for row in direction.shots
        )
    )
    if source_index_ranges:
        source_index_normalized = []
        for directed in direction.shots:
            source_shot = script_by_index.get(directed.source_shot_index)
            identities = (
                {
                    (
                        turn.speaker_name,
                        turn.delivery_mode,
                        turn.role,
                    )
                    for turn in source_shot.turns
                }
                if source_shot is not None
                else set()
            )
            if source_shot is not None and len(identities) == 1:
                directed = directed.model_copy(
                    update={
                        "turn_start": 1,
                        "turn_end": len(source_shot.turns),
                    }
                )
            source_index_normalized.append(directed)
        direction = direction.model_copy(
            update={"shots": source_index_normalized}
        )
    source_offsets = {}
    cursor = 0
    for source_shot in script.shots:
        source_offsets[source_shot.local_index] = cursor
        cursor += len(source_shot.turns)
    normalized_shots = []
    previous_close = None
    for directed in direction.shots:
        source_shot = next(
            (
                shot
                for shot in script.shots
                if shot.local_index == directed.source_shot_index
            ),
            None,
        )
        update = {}
        if source_shot is not None:
            offset = source_offsets[source_shot.local_index]
            if (
                (
                    directed.turn_start > len(source_shot.turns)
                    or directed.turn_end > len(source_shot.turns)
                )
                and offset + 1 <= directed.turn_start <= offset + len(source_shot.turns)
                and offset + 1 <= directed.turn_end <= offset + len(source_shot.turns)
            ):
                update.update(
                    {
                        "turn_start": directed.turn_start - offset,
                        "turn_end": directed.turn_end - offset,
                    }
                )
        if previous_close is not None:
            update["script_open_state"] = previous_close
        normalized = directed.model_copy(update=update)
        normalized_shots.append(normalized)
        previous_close = normalized.script_close_state
    direction = direction.model_copy(update={"shots": normalized_shots})
    tail_extended = list(direction.shots)
    for source_shot in script.shots:
        row_indexes = [
            index
            for index, row in enumerate(tail_extended)
            if row.source_shot_index == source_shot.local_index
        ]
        if not row_indexes:
            continue
        rows = [tail_extended[index] for index in row_indexes]
        expected_start = 1
        continuous = True
        for row in rows:
            if row.turn_start != expected_start:
                continuous = False
                break
            expected_start = row.turn_end + 1
        last = rows[-1]
        if (
            not continuous
            or last.turn_end >= len(source_shot.turns)
            or last.turn_end < 1
        ):
            continue
        anchor = source_shot.turns[last.turn_end - 1]
        missing = source_shot.turns[last.turn_end :]
        if missing and all(
            (
                turn.speaker_name,
                turn.delivery_mode,
                turn.role,
            )
            == (
                anchor.speaker_name,
                anchor.delivery_mode,
                anchor.role,
            )
            for turn in missing
        ):
            tail_extended[row_indexes[-1]] = last.model_copy(
                update={"turn_end": len(source_shot.turns)}
            )
    direction = direction.model_copy(update={"shots": tail_extended})
    issues = []
    if direction.beat_id != beat.beat_id:
        issues.append("beat_id must equal the requested retention beat")
    source_by_index = {shot.local_index: shot for shot in script.shots}
    ordered_ranges = [
        (shot.source_shot_index, shot.turn_start, shot.turn_end)
        for shot in direction.shots
    ]
    if ordered_ranges != sorted(ordered_ranges):
        issues.append("directed shots must preserve source-shot and turn order")
    for previous, current in zip(
        direction.shots,
        direction.shots[1:],
    ):
        if previous.script_close_state != current.script_open_state:
            issues.append(
                "directed shot handoff must match across knowledge, power, "
                "relationship, physical, and ongoing_action"
            )
    for source_index, source_shot in source_by_index.items():
        rows = [
            shot
            for shot in direction.shots
            if shot.source_shot_index == source_index
        ]
        if not rows:
            issues.append(f"source shot {source_index} has no direction")
            continue
        expected_start = 1
        for row in rows:
            if row.turn_start != expected_start:
                issues.append(
                    f"source shot {source_index} direction has a gap or overlap before "
                    f"turn {row.turn_start}"
                )
            expected_start = row.turn_end + 1
            direction_surface = f"{row.visual_prompt} {row.motion_prompt}"
            forbidden_surface = [
                token
                for token in ("指甲", "血珠", "流血", "伤口", "破皮")
                if token in direction_surface
            ]
            if forbidden_surface:
                issues.append(
                    f"source shot {source_index} direction uses non-generatable "
                    "injury detail: " + ",".join(forbidden_surface)
                )
            forbidden_temporal_visual = [
                token
                for token in (
                    "回忆画面",
                    "陷入回忆",
                    "回忆中的",
                    "少年版",
                    "少年楚焱",
                    "画面切换回现实",
                )
                if token in direction_surface
            ]
            if forbidden_temporal_visual:
                issues.append(
                    f"source shot {source_index} direction leaves the current "
                    "timeline: " + ",".join(forbidden_temporal_visual)
                )
            for motion_index, motion in enumerate(
                row.performance_plan.motion_beats,
                start=1,
            ):
                missing = [
                    field
                    for field, value in (
                        ("seconds", motion.seconds),
                        ("actor", motion.actor),
                        ("target", motion.target),
                        ("end_state", motion.end_state),
                    )
                    if value is None or value == ""
                ]
                if "action_type" not in motion.model_fields_set:
                    missing.append("action_type")
                if missing:
                    issues.append(
                        f"source shot {source_index} motion beat {motion_index} "
                        f"missing {','.join(missing)}"
                    )
                performance_text = " ".join(
                    (
                        motion.action,
                        motion.reaction,
                        motion.expression_transition,
                        motion.end_state,
                    )
                )
                forbidden_performance = [
                    token
                    for token in (
                        "血珠",
                        "流血",
                        "伤口",
                        "破皮",
                        "指甲",
                        "眉头微锁",
                        "眼睫微颤",
                        "喉结滚动",
                        "细微颤动",
                        "嘴角勾起",
                        "眼神空洞",
                    )
                    if token in performance_text
                ]
                if forbidden_performance:
                    issues.append(
                        f"source shot {source_index} motion beat {motion_index} uses "
                        "non-generatable micro or injury detail: "
                        + ",".join(forbidden_performance)
                    )
        if expected_start != len(source_shot.turns) + 1:
            issues.append(
                f"source shot {source_index} direction does not cover all turns"
            )
    unknown_sources = sorted(
        {shot.source_shot_index for shot in direction.shots} - set(source_by_index)
    )
    if unknown_sources:
        issues.append(f"direction references unknown source shots: {unknown_sources}")
    if native_dialogue and any(
        shot.audio_plan.speech_strategy != SpeechStrategy.NATIVE
        for shot in direction.shots
    ):
        issues.append("native_dialogue direction must use speech_strategy=native")
    if issues:
        raise ValueError(json.dumps({"beat_direction_errors": issues}, ensure_ascii=False))
    return direction


def _compile_retention_beat_episode(
    *,
    episode: Episode,
    diagnosis: ChapterDiagnosis,
    showrunner: ShowrunnerPlan,
    scripts: list[RetentionBeatScript],
    directions: list[RetentionBeatDirection],
    creative_profile: str,
    native_dialogue: bool,
    episode_contract: EpisodeContract | None = None,
) -> EpisodePlan:
    script_by_beat = {script.beat_id: script for script in scripts}
    direction_by_beat = {direction.beat_id: direction for direction in directions}
    shots: list[Shot] = []
    shot_indexes_by_beat: dict[str, list[int]] = {}
    for retention_beat in showrunner.retention.beats:
        script = script_by_beat[retention_beat.beat_id]
        direction = direction_by_beat[retention_beat.beat_id]
        source_by_index = {shot.local_index: shot for shot in script.shots}
        beat_indexes = []
        for directed in direction.shots:
            source = source_by_index[directed.source_shot_index]
            turns = source.turns[directed.turn_start - 1 : directed.turn_end]
            index = len(shots) + 1
            beat_indexes.append(index)
            lead_text = turns[0].text[:80]
            shots.append(
                Shot(
                    index=index,
                    narration=lead_text,
                    subtitle=lead_text,
                    visual_prompt=directed.visual_prompt,
                    motion_prompt=directed.motion_prompt,
                    characters=source.characters,
                    location=source.location,
                    source_quote=source.source_quote,
                    scene_job=source.scene_job,
                    change=source.change,
                    event_ids=source.event_ids,
                    shot_scale=directed.shot_scale,
                    turns=turns,
                    performance_plan=directed.performance_plan,
                    camera_plan=directed.camera_plan,
                    visual_strategy=directed.visual_strategy,
                    keyframe_reasons=directed.keyframe_reasons,
                    shot_intent=source.shot_intent,
                    audio_plan=directed.audio_plan,
                    script_open_state=directed.script_open_state,
                    script_close_state=directed.script_close_state,
                )
            )
        shot_indexes_by_beat[retention_beat.beat_id] = beat_indexes
    bound_retention = showrunner.retention.model_copy(
        update={
            "beats": [
                beat.model_copy(
                    update={"shot_indexes": shot_indexes_by_beat[beat.beat_id]}
                )
                for beat in showrunner.retention.beats
            ]
        }
    )
    bound_showrunner = showrunner.model_copy(update={"retention": bound_retention})
    ledger = []
    for event in diagnosis.events:
        indexes = [shot.index for shot in shots if event.event_id in shot.event_ids]
        ledger.append(
            AdaptationLedgerItem(
                event_id=event.event_id,
                disposition="preserved" if indexes else "removed",
                shot_indexes=indexes,
                rationale=(
                    "按RetentionBeat分段编写并在独立direction pass中保留"
                    if indexes
                    else "当前beat脚本未映射该非关键事件"
                ),
            )
        )
    first_beat = showrunner.retention.beats[0]
    diagnosed_characters = list(
        dict.fromkeys(
            character
            for event in diagnosis.events
            for character in event.characters
        )
    )
    opponent_name = (
        diagnosed_characters[1]
        if len(diagnosed_characters) >= 2
        else "当前章环境压力"
    )
    dramaturgy = EpisodeDramaturgy(
        genre_engine="retention-beat-pressure-loop",
        dramatic_question=first_beat.audience_question,
        cold_open=(
            shots[0].turns[0].text
            if shots and shots[0].turns
            else first_beat.promise
        ),
        cold_open_source_quote=(
            shots[0].turns[0].source_quote or shots[0].source_quote
            if shots and shots[0].turns
            else first_beat.source_quote
        ),
        status_before=diagnosis.chapter_start_state,
        status_after=diagnosis.chapter_end_state,
        conflict_beats=[beat.promise for beat in showrunner.retention.beats[:6]],
        reveal_order=[
            fact_id
            for beat in showrunner.retention.beats
            for fact_id in beat.new_information_fact_ids
        ],
        cliffhanger=showrunner.retention.ending_open_loop,
        narration_budget_ratio=0.0 if native_dialogue else 0.2,
        episode_mode=showrunner.episode_mode,
        protagonist_choice=showrunner.protagonist_choice,
        choice_source_quote=showrunner.choice_source_quote,
        cost_paid=showrunner.cost_paid,
        cost_source_quote=showrunner.cost_source_quote,
        opposition=(
            showrunner.opposition
            or EpisodeOpposition(
                opponent_name=opponent_name,
                goal=diagnosis.core_event,
                tactic=diagnosis.strongest_hook_candidate,
                source_event_ids=list(
                    dict.fromkeys(
                        event_id
                        for beat in showrunner.retention.beats
                        for event_id in beat.event_ids
                    )
                ),
            )
        ),
    )
    return EpisodePlan(
        video_title=episode.source_title,
        hook=first_beat.audience_question,
        summary=diagnosis.core_event,
        shots=shots,
        next_preview=showrunner.retention.ending_open_loop,
        adaptation_ledger=ledger,
        creative_profile=creative_profile,
        dramaturgy=dramaturgy,
        showrunner_plan=bound_showrunner,
        episode_contract=episode_contract,
    )


def _review_redundant_derived_candidates(
    plan: EpisodePlan,
    review: ScriptQualityReport,
) -> list[int]:
    event_counts: dict[str, int] = {}
    beat_counts: dict[str, int] = {}
    for shot in plan.shots:
        for event_id in shot.event_ids:
            event_counts[event_id] = event_counts.get(event_id, 0) + 1
        beat_id = shot.shot_intent.retention_beat_id
        if beat_id:
            beat_counts[beat_id] = beat_counts.get(beat_id, 0) + 1
    requested = sorted(
        {
            shot_index
            for issue in review.issues
            if issue.code
            in {"derived_serves_invalid", "derived_serves_mismatch"}
            for shot_index in issue.shot_indexes
        }
    )
    by_index = {shot.index: shot for shot in plan.shots}
    removable = []
    for shot_index in requested:
        shot = by_index.get(shot_index)
        if shot is None or shot_index in {1, len(plan.shots)}:
            continue
        beat_id = shot.shot_intent.retention_beat_id
        if (
            not shot.turns
            or not all(
                turn.derivation == TurnDerivation.DERIVED
                for turn in shot.turns
            )
            or shot.shot_intent.dramatic_function == "cliffhanger"
            or not shot.event_ids
            or any(event_counts.get(event_id, 0) <= 1 for event_id in shot.event_ids)
            or (beat_id and beat_counts.get(beat_id, 0) <= 1)
        ):
            continue
        removable.append(shot_index)
        for event_id in shot.event_ids:
            event_counts[event_id] -= 1
        if beat_id:
            beat_counts[beat_id] -= 1
    return removable


def _prune_redundant_derived_shots(
    plan: EpisodePlan,
    review: ScriptQualityReport,
) -> tuple[EpisodePlan, list[int]]:
    removed = _review_redundant_derived_candidates(plan, review)
    if not removed:
        return plan, []
    removed_set = set(removed)
    kept = [shot for shot in plan.shots if shot.index not in removed_set]
    index_map = {
        shot.index: index for index, shot in enumerate(kept, start=1)
    }
    rebound_shots = []
    previous_close = None
    for shot in kept:
        update = {"index": index_map[shot.index]}
        if previous_close is not None:
            update["script_open_state"] = previous_close
        rebound = shot.model_copy(update=update)
        rebound_shots.append(rebound)
        previous_close = rebound.script_close_state
    ledger = [
        item.model_copy(
            update={
                "shot_indexes": [
                    index_map[index]
                    for index in item.shot_indexes
                    if index in index_map
                ]
            }
        )
        for item in plan.adaptation_ledger
    ]
    showrunner = plan.showrunner_plan
    if showrunner is not None:
        showrunner = showrunner.model_copy(
            update={
                "retention": showrunner.retention.model_copy(
                    update={
                        "beats": [
                            beat.model_copy(
                                update={
                                    "shot_indexes": [
                                        index_map[index]
                                        for index in beat.shot_indexes
                                        if index in index_map
                                    ]
                                }
                            )
                            for beat in showrunner.retention.beats
                        ]
                    }
                )
            }
        )
    return (
        plan.model_copy(
            update={
                "shots": rebound_shots,
                "adaptation_ledger": ledger,
                "showrunner_plan": showrunner,
            }
        ),
        removed,
    )


def _downgrade_unremovable_review_deletion_claims(
    plan: EpisodePlan,
    review: ScriptQualityReport,
) -> ScriptQualityReport:
    removable = set(_review_redundant_derived_candidates(plan, review))
    issues = []
    for issue in review.issues:
        if (
            issue.code
            in {"derived_serves_invalid", "derived_serves_mismatch"}
            and issue.severity == "blocking"
            and not removable.intersection(issue.shot_indexes)
        ):
            issues.append(
                issue.model_copy(
                    update={
                        "code": "review_deletion_claim_not_supported",
                        "severity": "warning",
                        "gate_level": "craft",
                        "message": (
                            "reviewer建议删除，但按source trace删除会使当前事件或beat失去唯一载体；"
                            + issue.message
                        ),
                    }
                )
            )
        else:
            issues.append(issue)
    return review.model_copy(
        update={
            "passed": not any(
                issue.severity == "blocking" for issue in issues
            ),
            "issues": issues,
        }
    )


def _validate_series_development(
    data: dict,
    *,
    novel: NovelDocument,
    bible: StoryBible,
    diagnoses: list[ChapterDiagnosis],
    development_version: str,
) -> SeriesDevelopmentPlan:
    normalized = {**data}
    raw_projections = data.get("chapter_projections")
    if isinstance(raw_projections, list):
        projections = []
        for index, row in enumerate(raw_projections):
            if not isinstance(row, dict) or index >= len(diagnoses):
                projections.append(row)
                continue
            diagnosis = diagnoses[index]
            projections.append(
                {
                    **row,
                    "episode_index": index + 1,
                    "source_chapter": diagnosis.source_chapter,
                    "allowed_event_ids": [
                        event.event_id for event in diagnosis.events
                    ],
                    "required_close_state": diagnosis.chapter_end_state,
                }
            )
        normalized["chapter_projections"] = projections
    development = SeriesDevelopmentPlan.model_validate(normalized)
    issues = []
    if development.development_version != development_version:
        issues.append(f"development_version must be {development_version}")
    if development.novel_title != novel.title:
        issues.append("novel_title must match the source novel")
    expected_indexes = list(range(1, len(diagnoses) + 1))
    actual_indexes = [
        projection.episode_index for projection in development.chapter_projections
    ]
    if actual_indexes != expected_indexes:
        issues.append("chapter projections must cover every episode exactly once in order")
    obligations = {item.obligation_id: item for item in development.obligations}
    if len(obligations) != len(development.obligations):
        issues.append("obligation ids must be unique")
    projections = []
    for projection, diagnosis in zip(
        development.chapter_projections,
        diagnoses,
        strict=False,
    ):
        required_reveals = {
            event.event_id
            for event in diagnosis.events
            if event.importance == "critical"
        }
        for obligation_id in projection.setup_obligation_ids:
            obligation = obligations.get(obligation_id)
            if obligation is not None:
                required_reveals.update(obligation.source_event_ids)
        selected_reveals = set(projection.allowed_reveal_event_ids)
        allowed_reveals = [
            event.event_id
            for event in diagnosis.events
            if event.event_id
            in (selected_reveals | required_reveals)
        ]
        allowed_reveals.extend(
            event_id
            for event_id in projection.allowed_reveal_event_ids
            if event_id not in {event.event_id for event in diagnosis.events}
        )
        projections.append(
            projection.model_copy(
                update={"allowed_reveal_event_ids": allowed_reveals}
            )
        )
    development = development.model_copy(
        update={"chapter_projections": projections}
    )
    canonical_names = {character.name for character in bible.characters}
    for edge in development.relationship_pressure_network:
        unknown = set(edge.people) - canonical_names
        if unknown:
            issues.append(f"relationship pressure edge uses unknown people: {sorted(unknown)}")
    for projection, diagnosis in zip(
        development.chapter_projections,
        diagnoses,
        strict=False,
    ):
        event_ids = {event.event_id for event in diagnosis.events}
        if projection.source_chapter != diagnosis.source_chapter:
            issues.append(
                f"episode {projection.episode_index} source_chapter does not match diagnosis"
            )
        if set(projection.allowed_event_ids) != event_ids:
            issues.append(
                f"episode {projection.episode_index} allowed_event_ids must equal its own diagnosis events"
            )
        if set(projection.allowed_reveal_event_ids) - event_ids:
            issues.append(
                f"episode {projection.episode_index} reveal permission references another chapter"
            )
        if not projection.allowed_reveal_event_ids:
            issues.append(
                f"episode {projection.episode_index} must permit at least one current-chapter reveal event"
            )
        if projection.required_close_state != diagnosis.chapter_end_state:
            issues.append(
                f"episode {projection.episode_index} required_close_state must copy diagnosis end state"
            )
        unknown_obligations = (
            set(projection.setup_obligation_ids)
            | set(projection.payoff_obligation_ids)
        ) - set(obligations)
        if unknown_obligations:
            issues.append(
                f"episode {projection.episode_index} references unknown obligations: "
                f"{sorted(unknown_obligations)}"
            )
    for obligation in development.obligations:
        if obligation.setup_episode > len(diagnoses):
            issues.append(f"{obligation.obligation_id} setup episode is out of range")
            continue
        setup_events = {
            event.event_id
            for event in diagnoses[obligation.setup_episode - 1].events
        }
        if not set(obligation.source_event_ids) <= setup_events:
            issues.append(
                f"{obligation.obligation_id} source events do not belong to its setup episode"
            )
    if issues:
        raise ValueError(
            json.dumps({"series_development_errors": issues}, ensure_ascii=False)
        )
    return development


def _normalize_current_episode_state_quotes(
    data: dict,
    episode: Episode,
) -> tuple[dict, list[str]]:
    normalized = json.loads(json.dumps(data, ensure_ascii=False))
    source_key = _quote_key(episode.source_text)
    evidence = source_evidence_units(episode.source_text)
    changes: list[str] = []

    def visit(value, path: str) -> None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}.{index}" if path else str(index))
            return
        if not isinstance(value, dict):
            return
        if (
            value.get("source_episode") == episode.index
            and value.get("statement")
            and value.get("source_quote")
            and _quote_key(str(value["source_quote"])) not in source_key
            and evidence
        ):
            target = (
                str(value["statement"]) + " " + str(value["source_quote"])
            )
            value["source_quote"] = max(
                evidence,
                key=lambda row: SequenceMatcher(None, target, row).ratio(),
            )
            changes.append(path or "state")
        for key, item in value.items():
            visit(item, f"{path}.{key}" if path else key)

    visit(normalized, "")
    return normalized, changes


def _current_projection_context(
    development: SeriesDevelopmentPlan,
    episode_index: int,
) -> dict:
    projection = development.chapter_projections[episode_index - 1]
    obligations = {item.obligation_id: item for item in development.obligations}
    return {
        "development_version": development.development_version,
        "engine": development.engine.model_dump(mode="json"),
        "relationship_pressure_network": [
            edge.model_dump(mode="json")
            for edge in development.relationship_pressure_network
        ],
        "projection": projection.model_dump(mode="json"),
        "setup_obligations": [
            {
                "obligation_id": obligation_id,
                "setup_function": obligations[obligation_id].setup_function,
                "source_event_ids": obligations[obligation_id].source_event_ids,
            }
            for obligation_id in projection.setup_obligation_ids
        ],
        "payoff_obligations": [
            {
                "obligation_id": obligation_id,
                "payoff_function": obligations[obligation_id].payoff_function,
                "source_event_ids": obligations[obligation_id].source_event_ids,
            }
            for obligation_id in projection.payoff_obligation_ids
        ],
    }


def plan_episode_contract(
    *,
    development: SeriesDevelopmentPlan,
    diagnosis: ChapterDiagnosis,
    showrunner: ShowrunnerPlan,
    episode_index: int,
) -> EpisodeContract:
    projection = development.chapter_projections[episode_index - 1]
    if projection.source_chapter != diagnosis.source_chapter:
        raise ValueError("episode projection does not match the current diagnosis")
    allowed_events = set(projection.allowed_event_ids)
    for beat in showrunner.retention.beats:
        if not set(beat.event_ids) <= allowed_events:
            raise ValueError(f"{beat.beat_id} uses an event outside the current projection")
    allowed_reveals = set(projection.allowed_reveal_event_ids)
    allowed_fact_ids = []
    for fact in showrunner.information_states:
        if not set(fact.source_event_ids) <= allowed_reveals:
            raise ValueError(
                f"{fact.fact_id} reveals a fact outside the current chapter permission"
            )
        allowed_fact_ids.append(fact.fact_id)
    for delta in showrunner.character_state_deltas:
        if not set(delta.event_ids) <= allowed_events:
            raise ValueError(
                f"character delta for {delta.character_name} uses a future event"
            )
    return EpisodeContract(
        episode_index=episode_index,
        development_version=development.development_version,
        arc_position=projection.arc_position,
        pressure_loop=development.engine.pressure_loop,
        protagonist_default_strategy=development.engine.protagonist_default_strategy,
        strategy_creates_problem=development.engine.strategy_creates_problem,
        pressure_step=projection.pressure_step,
        setup_obligation_ids=projection.setup_obligation_ids,
        payoff_obligation_ids=projection.payoff_obligation_ids,
        allowed_event_ids=projection.allowed_event_ids,
        allowed_information_fact_ids=allowed_fact_ids,
        retention_beat_ids=[beat.beat_id for beat in showrunner.retention.beats],
        required_close_state=projection.required_close_state,
        episode_mode=showrunner.episode_mode,
        protagonist_choice=showrunner.protagonist_choice,
        cost_paid=showrunner.cost_paid,
        opposition=showrunner.opposition,
    )


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])|\n+", text)
    merged: list[str] = []
    for part in parts:
        clean = re.sub(r"\s+", "", part)
        if not clean:
            continue
        if re.fullmatch(r"[”’」』】）》]+", clean) and merged:
            merged[-1] += clean
        else:
            merged.append(clean)
    return merged


def _compact_excerpt(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    third = limit // 3
    middle = len(text) // 2
    return text[:third] + "\n[中段抽样]\n" + text[middle - third // 2:middle + third // 2] + "\n[结尾]\n" + text[-third:]


def _short_beats(sentences: list[str], limit: int = 80) -> list[str]:
    beats: list[str] = []
    for sentence in sentences:
        if len(sentence) <= limit:
            beats.append(sentence)
            continue
        clauses = re.findall(r".+?[，、：,]|.+$", sentence)
        current = ""
        for clause in clauses:
            if current and len(current) + len(clause) > limit:
                beats.append(current)
                current = ""
            if len(clause) <= limit:
                current += clause
            else:
                if current:
                    beats.append(current)
                    current = ""
                beats.extend(clause[index:index + limit] for index in range(0, len(clause), limit))
        if current:
            beats.append(current)
    return beats


def _fingerprint(title: str, style: str, characters: list[Character]) -> str:
    payload = title + style + "|".join(f"{c.name}:{c.appearance}:{c.wardrobe}" for c in characters)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _script_turns(sentence: str, character_names: list[str]) -> list[ScriptTurn]:
    """Extract explicit quoted speech; keep everything else as closed-mouth narration."""
    dialogue = re.search(r"[“\"](.+?)[”\"]", sentence)
    if not dialogue:
        return [ScriptTurn(text=sentence[:80], source_quote=sentence[:120])]
    prefix = sentence[: dialogue.start()]
    candidates = [
        name
        for name in character_names
        if re.search(rf"{re.escape(name)}.{{0,16}}(?:说|问|答|喊)", prefix)
    ]
    if not candidates:
        return [ScriptTurn(text=sentence[:80], source_quote=sentence[:120])]
    speaker = max(candidates, key=prefix.rfind)
    line = dialogue.group(1)
    narration_prefix = prefix.rstrip("：“\"")
    turns: list[ScriptTurn] = []
    if narration_prefix:
        turns.append(ScriptTurn(text=narration_prefix[:80], source_quote=sentence[:120]))
    turns.append(
        ScriptTurn(
            role=speaker,
            speaker_name=speaker,
            text=line[:500],
            speaking=True,
            emotion="符合原文语气",
            source_quote=sentence[:120],
        )
    )
    return turns


class Planner(ABC):
    @abstractmethod
    def build_bible(self, novel: NovelDocument) -> StoryBible: ...

    @abstractmethod
    def plan_episode(self, novel: NovelDocument, episode: Episode, bible: StoryBible) -> EpisodePlan: ...

    def plan_episode_bundle(
        self,
        novel: NovelDocument,
        episode: Episode,
        bible: StoryBible,
        previous_state: SeriesState | None = None,
    ) -> EpisodePlanningBundle:
        """Backward-compatible audited wrapper for deterministic/command planners."""

        diagnosis = deterministic_chapter_diagnosis(episode)
        plan = bind_deterministic_events(self.plan_episode(novel, episode, bible), diagnosis)
        profile = getattr(self, "creative_profile", "faithful-chronological-v1")
        plan = apply_creative_direction(
            plan,
            diagnosis,
            bible,
            profile=profile,
        )
        plan = normalize_chronological_plan(plan, diagnosis, episode)
        report = evaluate_script_quality(
            plan, diagnosis, episode, previous_state=previous_state
        )
        if not report.passed:
            raise ValueError(
                "script quality gate failed: "
                + json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
            )
        state = deterministic_series_state(episode, diagnosis, previous_state)
        return EpisodePlanningBundle(
            diagnosis=diagnosis,
            plan=plan,
            quality_report=report,
            updated_series_state=state,
        )


class DeterministicPlanner(Planner):
    def __init__(self, settings: Settings | None = None):
        self.creative_profile = (
            settings.creative_profile if settings is not None else "faithful-chronological-v1"
        )

    def build_bible(self, novel: NovelDocument) -> StoryBible:
        names = re.findall(
            r"(?:^|[，。！？：“”\n])([\u4e00-\u9fff]{2,3})(?=(?:低声|轻声|高声|冷冷地)?(?:说|问|答|喊)|看向|走出|走进|推开|发现|把|从)",
            novel.text,
        )
        stopwords = {"低声", "忽然", "就在", "这时", "只说", "故事", "第一章", "第二章"}
        names = [name for name in names if name not in stopwords]
        ordered = list(dict.fromkeys(names))[:4]
        characters = [
            Character(
                name=name,
                role="主要人物" if i < 2 else "配角",
                appearance=f"{name}，黑发，轮廓清晰，神态具有辨识度",
                wardrobe="与故事时代匹配的固定主色服装",
            )
            for i, name in enumerate(ordered)
        ]
        if not characters:
            characters = [Character(
                name="主角",
                role="主角",
                appearance="黑发青年，清晰稳定的东亚面孔，眼神坚定",
                wardrobe="深蓝与银灰配色的固定服装",
            )]
        location_matches = re.findall(
            r"[\u4e00-\u9fff]{0,6}(?:书店|办公室|广场|庭院|山谷|街道|学校|教室|医院|客厅|卧室|地下室|门外)",
            novel.text,
        )
        cleaned_locations = []
        for match in location_matches:
            clean = re.sub(r"^.*(?:推开|进入|走进|来到|离开|回到|站在|看向)", "", match.strip())
            if clean:
                cleaned_locations.append(clean)
        locations = list(dict.fromkeys(cleaned_locations))[:8]
        if not locations:
            locations = ["与原文一致的主要场景"]
        fingerprint = _fingerprint(novel.title, STYLE, characters)
        return StoryBible(
            novel_title=novel.title,
            genre="小说改编漫剧",
            visual_style=STYLE,
            palette="青蓝与暖金平衡，冲突场景使用克制的红色点缀",
            characters=characters,
            locations=locations,
            continuity_rules=[
                "同一角色的性别、年龄、脸型、发型和服装主色不得变化",
                "同一地点的空间结构、时间、天气与关键物品保持连续",
                "不得新增改变人物关系、关键事件、因果或结局的情节",
            ],
            style_fingerprint=fingerprint,
        )

    def plan_episode(self, novel: NovelDocument, episode: Episode, bible: StoryBible) -> EpisodePlan:
        episode_text = episode.source_text.strip()
        if episode_text.startswith(episode.source_title):
            episode_text = episode_text[len(episode.source_title):].lstrip()
        sentences = _short_beats(_sentences(episode_text))
        if not sentences:
            sentences = [episode.source_text.strip()]
        shots: list[Shot] = []
        for index, sentence in enumerate(sentences, 1):
            narration = sentence
            source_quote = sentence
            character_names = [c.name for c in bible.characters if c.name in sentence]
            location = next(
                (candidate for candidate in bible.locations if candidate in sentence),
                bible.locations[0] if bible.locations else "原文当前场景",
            )
            visual = safe_visual_prompt(
                f"{bible.visual_style}。{bible.palette}。剧情：{sentence}。"
                f"角色设定：{'；'.join(c.name + c.appearance + c.wardrobe for c in bible.characters[:3])}"
            )
            turns = _script_turns(sentence, [character.name for character in bible.characters])
            has_dialogue = any(turn.speaking for turn in turns)
            performance_plan = PerformancePlan(
                objective=f"用连续动作讲清“{sentence[:60]}”，不是动态照片",
                start_state="人物处于事件开始前一瞬，视线、手部和身体重心仍有动作空间",
                motion_beats=[
                    MotionBeat(
                        phase="opening",
                        trigger="事件或台词开始",
                        action="眼睛先移动，头部随后转向目标，肩膀和上身稍后跟随",
                        reaction="身体重心随观察或说话方向发生变化",
                    ),
                    MotionBeat(
                        phase="development",
                        trigger="人物确认当前事件",
                        action=f"完成与剧情直接相关的动作：{sentence[:100]}",
                        reaction="手部动作带动身体响应，道具、头发和衣物体现惯性",
                    ),
                    MotionBeat(
                        phase="resolution",
                        trigger="本镜信息表达完成",
                        action="动作减速并停在能承接下一镜的位置",
                        reaction="呼吸和次级运动自然收束",
                    ),
                ],
                end_state="人物完成本镜动作，事件结果和最终表情清楚可读",
            )
            camera_moves = index == 1 or any(
                token in sentence
                for token in (
                    "走",
                    "跑",
                    "追",
                    "转身",
                    "进入",
                    "离开",
                    "发现",
                    "出现",
                )
            )
            camera_plan = CameraPlan(
                mode="motivated_subtle" if camera_moves else "locked",
                motivation=(
                    "空间建立、人物位移或信息揭示需要一次克制慢推或短跟拍"
                    if camera_moves
                    else "人物表演承担画面动态，稳定人物和场景空间关系"
                ),
                action_axis=f"{location}首次建立的人物视线或运动轴同侧",
                screen_direction="保持人物左右位置、视线和运动方向连续",
                start_position="竖屏中近景，画面包含前景、中景和远景层次",
                camera_beats=[
                    CameraBeat(
                        phase="opening",
                        trajectory=(
                            "沿行动轴同侧极慢推近或短距离跟随主要位移"
                            if camera_moves
                            else "锁定机位，摄影机全程保持静止"
                        ),
                        framing="只服务当前主要动作，保持人物屏幕侧和视线连续",
                        parallax=(
                            f"{location}固定空间锚点产生轻微自然视差"
                            if camera_moves
                            else f"不制造摄影机视差，{location}前中远景保持固定"
                        ),
                    ),
                    CameraBeat(
                        phase="resolution",
                        trajectory=(
                            "唯一一次慢推或跟拍完成后减速停住"
                            if camera_moves
                            else "继续锁定机位，让动作结果和表情停留一拍"
                        ),
                        framing="读清动作结果后稳定停留一拍",
                        parallax="背景结构和人物屏幕位置保持稳定",
                    ),
                ],
                end_position="行动轴同侧的稳定落点",
            )
            shots.append(Shot(
                index=index,
                narration=narration,
                subtitle=narration,
                visual_prompt=visual,
                motion_prompt=(
                    "摄影机克制跟随主要动作，人物通过视线、手势和身体重心完成有因果的表演，保持脸部和服装稳定"
                    if camera_moves
                    else "人物通过视线、手势和身体重心完成有因果的表演，保持脸部和服装稳定"
                ),
                characters=character_names,
                location=location,
                source_quote=source_quote,
                change=f"观众看到或得知：{sentence[:100]}",
                turns=turns,
                performance_plan=performance_plan,
                camera_plan=camera_plan,
            ))
        title_hint = episode.source_title if episode.source_title else shots[0].subtitle[:12]
        return EpisodePlan(
            video_title=title_hint[:30],
            hook=shots[0].narration,
            summary="".join(shot.narration for shot in shots)[:240],
            shots=shots,
        )


class OpenAICompatiblePlanner(Planner):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(timeout=settings.request_timeout)

    def _ensure_series_development(
        self,
        novel: NovelDocument,
        bible: StoryBible,
    ) -> tuple[SeriesDevelopmentPlan, list[ChapterDiagnosis]]:
        cached = getattr(self, "_series_development_cache", None)
        if cached is not None:
            return cached
        root = (
            self.settings.output_root.resolve()
            / novel.novel_id
            / "series_development"
        )
        active_path = root / "active.json"
        source_identity = hashlib.sha256(
            (
                novel.text
                + bible.style_fingerprint
                + "series-development-v2"
            ).encode("utf-8")
        ).hexdigest()
        if active_path.is_file():
            active = json.loads(active_path.read_text(encoding="utf-8"))
            version = str(active.get("development_version", ""))
            development_path = root / f"series_development.{version}.json"
            review_path = root / f"series_development_review.{version}.json"
            diagnoses_path = root / f"chapter_diagnoses.{version}.json"
            if (
                active.get("source_identity") == source_identity
                and development_path.is_file()
                and review_path.is_file()
                and diagnoses_path.is_file()
            ):
                diagnoses = [
                    ChapterDiagnosis.model_validate(row)
                    for row in json.loads(
                        diagnoses_path.read_text(encoding="utf-8")
                    )
                ]
                development = _validate_series_development(
                    json.loads(development_path.read_text(encoding="utf-8")),
                    novel=novel,
                    bible=bible,
                    diagnoses=diagnoses,
                    development_version=version,
                )
                review = SeriesDevelopmentReview.model_validate_json(
                    review_path.read_text(encoding="utf-8")
                )
                if review.passed and not review.future_fact_leakage:
                    self._series_development_cache = (development, diagnoses)
                    return development, diagnoses
        versions = [
            int(match.group(1))
            for path in root.glob("series_development.v*.json")
            if (match := re.search(r"\.v(\d{3})\.json$", path.name))
        ] if root.exists() else []
        version = f"v{max(versions, default=0) + 1:03d}"
        diagnoses = [
            self._diagnose_episode(episode, bible, None)
            for episode in novel.episodes
        ]
        system = (
            "你是系列故事开发负责人。输入只有各章紧凑诊断；输出一次性的系列压力引擎、"
            "主角默认策略为何制造新问题、升级阶梯、关系压力网、setup/payoff义务和逐章投影。"
            "不要写分镜、台词、机位或生产prompt。每章projection的allowed_event_ids必须只取该章诊断，"
            "allowed_reveal_event_ids只列本章允许Showrunner公开成fact的事件；不得把后文章节事实塞进前章投影。"
            "本轮保持一章一集。严格输出JSON。"
        )
        user = (
            f"小说标题：{novel.title}\n"
            f"development_version：{version}\n"
            f"角色圣经：{bible.model_dump_json()}\n"
            f"全部章节诊断：{json.dumps([row.model_dump(mode='json') for row in diagnoses], ensure_ascii=False)}\n"
            f"JSON Schema：{json.dumps(SeriesDevelopmentPlan.model_json_schema(), ensure_ascii=False)}"
        )
        development_attempt = max(
            (
                int(match.group(1))
                for path in root.glob(
                    f"series_development.{version}.attempt_*.raw.json"
                )
                if (
                    match := re.search(
                        r"\.attempt_(\d+)\.raw\.json$", path.name
                    )
                )
            ),
            default=0,
        )

        def request_development(repair: dict | None) -> dict:
            nonlocal development_attempt
            development_attempt += 1
            data = self._json(
                system,
                user,
                repair,
                token_budget=SERIES_DEVELOPMENT_TOKEN_BUDGET,
            )
            atomic_write_json(
                root
                / (
                    f"series_development.{version}.attempt_"
                    f"{development_attempt:02d}.raw.json"
                ),
                data,
            )
            return data

        development = _bounded_validate(
            "develop_series",
            self.settings.planner_max_revisions,
            request_development,
            lambda data: _validate_series_development(
                data,
                novel=novel,
                bible=bible,
                diagnoses=diagnoses,
                development_version=version,
            ),
        )
        atomic_write_json(
            root / f"series_development.{version}.candidate.json",
            development.model_dump(mode="json"),
        )
        review_system = (
            "你是独立系列开发审稿人。检查压力引擎是否可持续、默认策略是否真的制造新问题、"
            "升级阶梯是否递进、关系压力是否可执行、setup/payoff义务是否有归属，尤其检查每章投影"
            "是否泄漏后文章节事实。只审不改，严格输出JSON。"
        )
        review_attempt = max(
            (
                int(match.group(1))
                for path in root.glob(
                    f"series_development_review.{version}.attempt_*.raw.json"
                )
                if (
                    match := re.search(
                        r"\.attempt_(\d+)\.raw\.json$", path.name
                    )
                )
            ),
            default=0,
        )

        def request_review(repair: dict | None) -> dict:
            nonlocal review_attempt
            review_attempt += 1
            data = self._json(
                review_system,
                (
                    f"系列开发：{development.model_dump_json()}\n"
                    f"章节诊断：{json.dumps([row.model_dump(mode='json') for row in diagnoses], ensure_ascii=False)}\n"
                    f"JSON Schema：{json.dumps(SeriesDevelopmentReview.model_json_schema(), ensure_ascii=False)}"
                ),
                repair,
                token_budget=SERIES_DEVELOPMENT_REVIEW_TOKEN_BUDGET,
            )
            atomic_write_json(
                root
                / (
                    f"series_development_review.{version}.attempt_"
                    f"{review_attempt:02d}.raw.json"
                ),
                data,
            )
            return data

        review = _bounded_validate(
            "review_series_development",
            self.settings.planner_max_revisions,
            request_review,
            SeriesDevelopmentReview.model_validate,
        )
        atomic_write_json(
            root / f"series_development_review.{version}.candidate.json",
            review.model_dump(mode="json"),
        )
        if (
            not review.passed
            or not review.engine_coherent
            or not review.projections_grounded
            or review.future_fact_leakage
        ):
            raise EpisodePlanningFailed(
                "series development review failed",
                episode_index=1,
                failed_stage="series_development_review",
                attempts=self.settings.planner_max_revisions + 1,
                elapsed_seconds=0.0,
                intermediate_root=root,
            )
        atomic_write_json(
            root / f"chapter_diagnoses.{version}.json",
            [row.model_dump(mode="json") for row in diagnoses],
        )
        atomic_write_json(
            root / f"series_development.{version}.json",
            development.model_dump(mode="json"),
        )
        atomic_write_json(
            root / f"series_development_review.{version}.json",
            review.model_dump(mode="json"),
        )
        atomic_write_json(
            active_path,
            {
                "schema_version": 1,
                "development_version": version,
                "source_identity": source_identity,
                "review_passed": True,
            },
        )
        self._series_development_cache = (development, diagnoses)
        return development, diagnoses

    def _json(
        self,
        system: str,
        user: str,
        repair: dict | None = None,
        *,
        token_budget: int | None = None,
    ) -> dict:
        _check_planning_deadline(self, "llm_json_call")
        base = str(self.settings.llm_base_url).rstrip("/")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if repair:
            feedback = json.dumps(repair["validation_errors"], ensure_ascii=False)
            semantic_repair = any(
                code in feedback
                for code in (
                    "MISSING_CAUSALITY",
                    "CAUSAL_GAP",
                    "CHARACTER_MOTIVATION",
                    "causal_chain_broken",
                )
            )
            semantic_guidance = (
                "错误发生在某镜时，先找出观众理解该揭示或行动所必需的前置事实、关系和代价，"
                "并把这些内容落实到更小shot index的镜头；后置台词不能反向补足此前的动机。"
                "必须修改可检查的shots、event_ids、source_quote、动作或反应设计，"
                "不得只润色被点名镜头的措辞。"
                if semantic_repair
                else ""
            )
            if "causal_chain_broken" in feedback:
                semantic_guidance += (
                    "对于反馈中的‘结果事件缺少前置事件’pair，必须把前置event_id加入更小shot index的镜头，"
                    "并同步修正adaptation_ledger；只解释原因但不绑定event_ids不算修复。"
                )
            if "narrator_summarises_dialogue" in feedback:
                semantic_guidance += (
                    "凡旁白概括原文引号对白的镜头，删除概括句，按source_quote把原文对白逐条完整恢复给具体角色；"
                    "每条使用derivation=verbatim，不得合并、删词或同义改写。"
                )
            # A resample deliberately starts clean: carrying the rejected draft
            # would both anchor the model to it and, at roughly 25k tokens for
            # a full screenplay, overflow the context window once the output
            # budget is added.
            if repair.get("previous_response") is not None and not repair.get("resample"):
                messages.append({
                    "role": "assistant",
                    "content": json.dumps(repair["previous_response"], ensure_ascii=False),
                })
            if repair.get("resample"):
                messages.append({
                    "role": "user",
                    "content": (
                        "上一稿未通过校验，请重新独立创作一稿，不要沿用上一稿的写法。"
                        + semantic_guidance
                        + "需要避免的问题："
                        + feedback[:1200]
                    ),
                })
            else:
                messages.append({
                    "role": "user",
                    "content": (
                        "上一次 JSON 未通过确定性校验。只修复列出的错误，继续忠于输入原文，"
                        "不要解释、不要输出 Markdown。"
                        + semantic_guidance
                        + "校验反馈："
                        + feedback
                    ),
                })
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0.2,
            "max_tokens": min(
                self.settings.llm_max_tokens,
                token_budget or self.settings.llm_max_tokens,
            ),
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        if repair and repair.get("resample"):
            # A resample only helps if it can actually diverge; at temperature
            # 0.2 the model reproduces its previous answer almost verbatim,
            # which is how three "revisions" came back byte-identical.  A seed
            # derived from the attempt number is not enough either: the prompt
            # is identical across resamples, so the sampler has to be told to
            # draw fresh each time.
            payload["temperature"] = 1.0
            payload["top_p"] = 0.95
            payload["seed"] = random.randint(1, 2**31 - 1)
        if self.settings.llm_disable_thinking:
            # vLLM/Qwen accepts this OpenAI-compatible extension.  Keep it
            # opt-in so hosted OpenAI-compatible providers are unaffected.
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        response = self.client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
            json=payload,
        )
        _check_planning_deadline(self, "llm_json_call")
        if getattr(response, "status_code", 200) == 400:
            try:
                error_message = str(response.json()["error"]["message"])
            except (KeyError, TypeError, ValueError):
                error_message = ""
            context_match = re.search(
                r"maximum context length is (\d+) tokens", error_message
            )
            input_match = re.search(
                r"prompt contains at least (\d+) input tokens", error_message
            )
            if context_match and input_match:
                adjusted = max(
                    512,
                    min(
                        18000,
                        int(context_match.group(1))
                        - int(input_match.group(1))
                        - 1024,
                    ),
                )
                if adjusted < int(payload["max_tokens"]):
                    payload["max_tokens"] = adjusted
                    response = self.client.post(
                        f"{base}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.settings.llm_api_key}"
                        },
                        json=payload,
                    )
        _check_planning_deadline(self, "llm_json_call")
        try:
            response.raise_for_status()
        except Exception as error:
            # httpx names the status and nothing else; the server's body is
            # where the actual complaint is, and without it a 400 from the
            # inference server is undiagnosable from the logs.
            body = ""
            try:
                body = response.text[:600]
            except Exception:
                pass
            raise RuntimeError(f"{error}; body: {body}") from error
        content = response.json()["choices"][0]["message"]["content"]
        return _loads_json_object(content)

    def build_bible(self, novel: NovelDocument) -> StoryBible:
        schema = StoryBible.model_json_schema()
        system = (
            "你是漫剧总美术和小说事实核验员。只提取原文可支持的信息；外貌未写明时可做克制设计。"
            "每名主要角色还要建立可跨集复用的选角档案：visual_archetype写社会与戏剧类型，"
            "face_anchors写3-5个不可漂移的五官锚点，silhouette、hair、palette和base_costume必须彼此可区分，"
            "signature_prop只填写原文支持或不改变剧情的识别物，expression_profile描述表情幅度，"
            "motion_signature描述角色惯用姿态和动作节奏，voice_profile_id填写稳定的声音角色标识。"
            "场景locations应是可生成空场资产的地点名，不把人物动作写进地点。"
            "所有角色必须是健康、非色情、非血腥的统一国漫画风。严格输出 JSON。"
        )
        user = (
            f"小说名：{novel.title}\n文本：{_compact_excerpt(novel.text)}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}\n"
            f"visual_style 必须包含：{STYLE}。style_fingerprint 暂填空字符串。"
        )
        return _bounded_validate(
            "build_bible",
            self.settings.planner_max_revisions,
            lambda repair: self._json(
                system, user, repair, token_budget=DIAGNOSIS_TOKEN_BUDGET
            ),
            lambda data: _validate_story_bible(data, novel),
        )

    @staticmethod
    def _canonical_character_name(name: str, canonical_names: list[str]) -> str:
        normalized = re.sub(r"\s+", "", name)
        if name in canonical_names:
            return name
        emitted_aliases = {
            re.sub(r"\s+", "", item)
            for item in re.split(r"[/／|、]", name)
            if item.strip()
        }
        matches = []
        for canonical in canonical_names:
            normalized_canonical = re.sub(r"\s+", "", canonical)
            aliases = [
                re.sub(r"\s+", "", item)
                for item in re.split(r"[/／|、]", canonical)
                if item.strip()
            ]
            if (
                normalized in aliases
                or normalized in normalized_canonical
                or normalized_canonical in emitted_aliases
            ):
                matches.append(canonical)
        return matches[0] if len(matches) == 1 else name

    @staticmethod
    def _is_anonymous_crowd(name: str) -> bool:
        return bool(
            re.fullmatch(
                r"(?:(?:楚家)?(?:路人|族人|人群|围观者|旁人|少年|少女|弟子|群众)|中年族人)"
                r"[甲乙丙丁戊己庚辛壬癸一二三四五六七八九十\d]*",
                re.sub(r"\s+", "", name),
            )
        )

    @staticmethod
    def _location_key(name: str) -> str:
        value = re.sub(r"\s+", "", name)
        value = re.sub(r"[江河溪湖海]", "水", value)
        return re.sub(r"[的之]", "", value)

    @classmethod
    def _canonical_location_name(cls, name: str, canonical_names: list[str]) -> str:
        if not name or name in canonical_names:
            return name
        key = cls._location_key(name)
        matches = [
            canonical
            for canonical in canonical_names
            if key in cls._location_key(canonical)
            or cls._location_key(canonical) in key
        ]
        return matches[0] if len(matches) == 1 else name

    @staticmethod
    def _dialogue_source_span(text: str, source: str) -> str | None:
        """Find one contiguous source span containing ordered quoted speech.

        Novel dialogue is often interrupted by tags such as “他说道”. A speech
        turn may join those adjacent quotes while its evidence span keeps the
        intervening prose, preserving an exact source trace.
        """

        target = re.sub(r"\s+", "", text)
        quoted = list(re.finditer(r"[“\"]([^”\"]+)[”\"]", source))
        for start in range(len(quoted)):
            joined = ""
            for end in range(start, min(start + 3, len(quoted))):
                joined += re.sub(r"\s+", "", quoted[end].group(1))
                if joined == target or (len(target) >= 6 and target in joined):
                    span = source[quoted[start].start() : quoted[end].end()]
                    return span if len(span) <= 500 else None
                if len(joined) > len(target):
                    break
        return None

    def _canonicalize_characters(self, plan: EpisodePlan, bible: StoryBible) -> EpisodePlan:
        canonical_names = [character.name for character in bible.characters]
        canonical_locations = [
            self._canonical_location_name(shot.location, bible.locations)
            for shot in plan.shots
        ]
        for index, location in enumerate(canonical_locations):
            if location in bible.locations:
                continue
            before = next(
                (
                    candidate
                    for candidate in reversed(canonical_locations[:index])
                    if candidate in bible.locations
                ),
                None,
            )
            if before is not None and re.search(
                r"(?:回忆|闪回|虚空|意识|梦境)", location
            ):
                canonical_locations[index] = before
                continue
            adjacent_key = re.sub(
                r"(?:外|外侧|出口)$", "", self._location_key(location)
            )
            if (
                before is not None
                and adjacent_key
                and adjacent_key in self._location_key(before)
            ):
                canonical_locations[index] = before
        shots = []
        for shot, location in zip(plan.shots, canonical_locations):
            turns = []
            for turn in shot.turns:
                if turn.speaking:
                    speaker = self._canonical_character_name(turn.speaker_name, canonical_names)
                    if speaker not in canonical_names and self._is_anonymous_crowd(speaker):
                        turns.append(
                            turn.model_copy(
                                update={
                                    "speaker_name": "旁白",
                                    "role": "narrator",
                                    "speaking": False,
                                    "emotion": f"画外群声·{turn.emotion}",
                                }
                            )
                        )
                    else:
                        turns.append(
                            turn.model_copy(update={"speaker_name": speaker, "role": speaker})
                        )
                else:
                    turns.append(turn)
            characters = []
            for character in shot.characters:
                canonical = self._canonical_character_name(character, canonical_names)
                if canonical not in canonical_names and self._is_anonymous_crowd(canonical):
                    continue
                if canonical not in characters:
                    characters.append(canonical)
            shots.append(
                shot.model_copy(
                    update={"characters": characters, "turns": turns, "location": location}
                )
            )
        showrunner = plan.showrunner_plan
        if showrunner is not None:
            information_states = []
            for fact in showrunner.information_states:
                awareness = [
                    item.model_copy(
                        update={
                            "character_name": self._canonical_character_name(
                                item.character_name, canonical_names
                            )
                        }
                    )
                    for item in fact.character_awareness
                ]
                information_states.append(
                    fact.model_copy(update={"character_awareness": awareness})
                )
            deltas = [
                delta.model_copy(
                    update={
                        "character_name": self._canonical_character_name(
                            delta.character_name, canonical_names
                        )
                    }
                )
                for delta in showrunner.character_state_deltas
            ]
            showrunner = showrunner.model_copy(
                update={
                    "information_states": information_states,
                    "character_state_deltas": deltas,
                }
            )
        return plan.model_copy(update={"shots": shots, "showrunner_plan": showrunner})

    def _ground_quotes(self, plan: EpisodePlan, source: str, bible: StoryBible) -> EpisodePlan:
        plan = self._canonicalize_characters(plan, bible)
        normalized_source = re.sub(r"\s+", "", source)
        source_sentences = _sentences(source)
        grounded: list[Shot] = []
        for shot in plan.shots:
            quote = re.sub(r"\s+", "", shot.source_quote)
            if quote not in normalized_source:
                quote = max(
                    source_sentences,
                    key=lambda item: SequenceMatcher(None, item, shot.narration).ratio(),
                    default=source[:80],
                )[:110]
            grounded_turns = []
            for turn in shot.turns:
                turn_quote = re.sub(r"\s+", "", turn.source_quote)
                turn_source_quote = (
                    turn.source_quote
                    if turn_quote and turn_quote in normalized_source
                    else quote
                )
                if turn.speaking and re.sub(r"\s+", "", turn.text) not in re.sub(
                    r"\s+", "", turn_source_quote
                ):
                    dialogue_span = self._dialogue_source_span(turn.text, source)
                    if dialogue_span is not None:
                        turn_source_quote = dialogue_span
                grounded_turns.append(
                    turn.model_copy(update={"source_quote": turn_source_quote})
                )
            grounded.append(shot.model_copy(update={
                "source_quote": quote,
                "visual_prompt": safe_visual_prompt(shot.visual_prompt),
                "turns": grounded_turns,
            }))
        return plan.model_copy(update={"shots": grounded})

    def _validate_episode_data(
        self,
        data: dict,
        episode: Episode,
        bible: StoryBible,
    ) -> EpisodePlan:
        plan = self._ground_quotes(
            self._canonicalize_characters(EpisodePlan.model_validate(data), bible),
            episode.source_text,
            bible,
        )
        # Correct the decidable mislabels before judging the draft, so revision
        # rounds are spent on writing rather than on bookkeeping the controller
        # can do itself.
        plan = repair_machine_draft(plan, episode)
        plan = plan.model_copy(
            update={
                "shots": [
                    shot
                    if shot.change.strip()
                    else shot.model_copy(
                        update={
                            "change": (
                                f"本镜结束时：{shot.scene_job}；"
                                f"{shot.shot_intent.viewer_focus}"
                            )[:240]
                        }
                    )
                    for shot in plan.shots
                ]
            }
        )
        normalized_source = re.sub(r"\s+", "", episode.source_text)
        canonical_names = {character.name for character in bible.characters}
        canonical_locations = set(bible.locations)
        issues: list[dict[str, object]] = []
        if plan.showrunner_plan is not None:
            for fact_index, fact in enumerate(plan.showrunner_plan.information_states):
                unknown_awareness = sorted(
                    {
                        item.character_name
                        for item in fact.character_awareness
                        if item.character_name not in canonical_names
                    }
                )
                if unknown_awareness:
                    issues.append({
                        "field": (
                            f"showrunner_plan.information_states.{fact_index}."
                            "character_awareness"
                        ),
                        "message": "must use StoryBible character names",
                        "unknown": unknown_awareness,
                    })
            for delta_index, delta in enumerate(
                plan.showrunner_plan.character_state_deltas
            ):
                if delta.character_name not in canonical_names:
                    issues.append({
                        "field": (
                            f"showrunner_plan.character_state_deltas.{delta_index}."
                            "character_name"
                        ),
                        "message": "must use a StoryBible character name",
                        "unknown": delta.character_name,
                    })
        for shot in plan.shots:
            shot_quote = re.sub(r"\s+", "", shot.source_quote)
            if not shot_quote or shot_quote not in normalized_source:
                issues.append({
                    "field": f"shots.{shot.index}.source_quote",
                    "message": "must be an exact excerpt of this episode source",
                })
            unknown_characters = sorted(set(shot.characters) - canonical_names)
            if unknown_characters:
                issues.append({
                    "field": f"shots.{shot.index}.characters",
                    "message": "must use StoryBible character names",
                    "unknown": unknown_characters,
                })
            if shot.location and shot.location not in canonical_locations:
                issues.append({
                    "field": f"shots.{shot.index}.location",
                    "message": "must use a StoryBible location",
                    "unknown": shot.location,
                })
            if shot.performance_plan is None:
                issues.append({
                    "field": f"shots.{shot.index}.performance_plan",
                    "message": "is required and must describe causal action beats",
                })
            if shot.camera_plan is None:
                issues.append({
                    "field": f"shots.{shot.index}.camera_plan",
                    "message": (
                        "is required and must choose locked, motivated_subtle, or "
                        "motivated_emphasis with a narrative motivation and stable action axis"
                    ),
                })
            for turn_index, turn in enumerate(shot.turns):
                turn_quote = re.sub(r"\s+", "", turn.source_quote)
                if not turn_quote or turn_quote not in normalized_source:
                    issues.append({
                        "field": f"shots.{shot.index}.turns.{turn_index}.source_quote",
                        "message": "must be an exact excerpt of this episode source",
                    })
                if turn.speaking and turn.speaker_name not in canonical_names:
                    issues.append({
                        "field": f"shots.{shot.index}.turns.{turn_index}.speaker_name",
                        "message": "visible speaker must use a StoryBible character name",
                        "unknown": turn.speaker_name,
                    })
                if (
                    turn.speaking
                    and turn.derivation == TurnDerivation.VERBATIM
                    and re.sub(r"\s+", "", turn.text) not in turn_quote
                    and self._dialogue_source_span(turn.text, turn.source_quote) is None
                ):
                    issues.append({
                        "field": f"shots.{shot.index}.turns.{turn_index}.text",
                        "message": "visible dialogue must occur verbatim inside "
                        "source_quote; if this line stages narration as speech, "
                        "declare derivation=derived and cite that narration",
                    })
        if issues:
            raise ValueError(json.dumps({"domain_errors": issues}, ensure_ascii=False))
        return plan

    def _validate_showrunner_data(
        self,
        data: dict,
        episode: Episode,
        diagnosis: ChapterDiagnosis,
        bible: StoryBible,
        allowed_reveal_event_ids: set[str] | None = None,
        require_episode_intent: bool = False,
    ) -> ShowrunnerPlan:
        normalized = {**data}
        retention = data.get("retention")
        if isinstance(retention, dict):
            beats = retention.get("beats")
            if isinstance(beats, list):
                normalized["retention"] = {
                    **retention,
                    "beats": [
                        (
                            {**beat, "function": "payoff"}
                            if isinstance(beat, dict)
                            and beat.get("function") == "reveal"
                            else beat
                        )
                        for beat in beats
                    ],
                }
        if allowed_reveal_event_ids is not None:
            event_importance = {
                event.event_id: event.importance
                for event in diagnosis.events
            }
            raw_facts = data.get("information_states")
            if isinstance(raw_facts, list):
                facts = []
                for fact in raw_facts:
                    if not isinstance(fact, dict):
                        facts.append(fact)
                        continue
                    source_ids = list(fact.get("source_event_ids", []))
                    allowed_ids = [
                        event_id
                        for event_id in source_ids
                        if event_id in allowed_reveal_event_ids
                    ]
                    if (
                        allowed_ids
                        and len(allowed_ids) < len(source_ids)
                        and any(
                            event_importance.get(event_id) == "critical"
                            for event_id in allowed_ids
                        )
                    ):
                        facts.append(
                            {**fact, "source_event_ids": allowed_ids}
                        )
                    else:
                        facts.append(fact)
                normalized["information_states"] = facts
        showrunner = ShowrunnerPlan.model_validate(normalized)
        canonical_names = [character.name for character in bible.characters]
        information_states = []
        for fact in showrunner.information_states:
            awareness = [
                item.model_copy(
                    update={
                        "character_name": self._canonical_character_name(
                            item.character_name, canonical_names
                        )
                    }
                )
                for item in fact.character_awareness
            ]
            information_states.append(
                fact.model_copy(update={"character_awareness": awareness})
            )
        deltas = [
            delta.model_copy(
                update={
                    "character_name": self._canonical_character_name(
                        delta.character_name, canonical_names
                    )
                }
            )
            for delta in showrunner.character_state_deltas
        ]
        showrunner = showrunner.model_copy(
            update={
                "planning_mode": "planner",
                "information_states": information_states,
                "character_state_deltas": deltas,
            }
        )
        source = re.sub(r"\s+", "", episode.source_text)
        event_ids = {event.event_id for event in diagnosis.events}
        issues: list[dict[str, object]] = []

        def grounded(value: str) -> bool:
            return bool(value and re.sub(r"\s+", "", value) in source)

        # Character names already get controller-side canonicalisation above;
        # quotes relied on the model copying chapter text to the character.
        # A 27B planner reliably paraphrases one beat per attempt and the
        # repair loop never converges.  The semantic binding is event_ids —
        # validated on their own below — so when a quote is not verbatim but
        # the entry points at diagnosed events, substitute the event's already
        # validated source_quote instead of failing the whole stage.
        event_quotes = {event.event_id: event.source_quote for event in diagnosis.events}

        def anchored_quote(quote: str, ids: list[str]) -> str:
            if grounded(quote):
                return quote
            for event_id in ids:
                candidate = event_quotes.get(event_id, "")
                if grounded(candidate):
                    return candidate
            return quote

        showrunner = showrunner.model_copy(
            update={
                "retention": showrunner.retention.model_copy(
                    update={
                        "beats": [
                            beat.model_copy(
                                update={
                                    "source_quote": anchored_quote(
                                        beat.source_quote, beat.event_ids
                                    )
                                }
                            )
                            for beat in showrunner.retention.beats
                        ]
                    }
                ),
                "information_states": [
                    fact.model_copy(
                        update={
                            "source_quote": anchored_quote(
                                fact.source_quote, fact.source_event_ids
                            )
                        }
                    )
                    for fact in showrunner.information_states
                ],
                "character_state_deltas": [
                    delta.model_copy(
                        update={
                            "source_quote": anchored_quote(
                                delta.source_quote, delta.event_ids
                            )
                        }
                    )
                    for delta in showrunner.character_state_deltas
                ],
            }
        )
        fact_ids = {fact.fact_id for fact in showrunner.information_states}
        beat_ids = {beat.beat_id for beat in showrunner.retention.beats}

        starts = [beat.target_start_ratio for beat in showrunner.retention.beats]
        points = [0.0, *starts, 1.0]
        max_gap = max(
            (right - left for left, right in zip(points, points[1:])),
            default=1.0,
        )
        if max_gap > showrunner.retention.max_attention_gap_ratio + 0.01:
            issues.append({
                "field": "retention.beats",
                "message": "retention beats leave a middle attention gap above budget",
                "max_gap_ratio": round(max_gap, 6),
            })
        functions = {beat.function for beat in showrunner.retention.beats}
        if not {"hook", "question", "cliffhanger"} <= functions or not functions & {
            "payoff",
            "reversal",
        }:
            issues.append({
                "field": "retention.beats",
                "message": "must include hook, question, payoff or reversal, and cliffhanger",
            })
        if not any(
            beat.function == "hook" and beat.target_start_ratio <= 0.05
            for beat in showrunner.retention.beats
        ):
            issues.append({"field": "retention.beats", "message": "hook must start in first 5%"})
        if not any(
            beat.function == "cliffhanger" and beat.target_start_ratio >= 0.8
            for beat in showrunner.retention.beats
        ):
            issues.append({
                "field": "retention.beats",
                "message": "cliffhanger must start in final 20%",
            })
        for index, beat in enumerate(showrunner.retention.beats):
            if beat.shot_indexes:
                issues.append({
                    "field": f"retention.beats.{index}.shot_indexes",
                    "message": "must stay empty until screenplay shots exist",
                })
            if not grounded(beat.source_quote):
                issues.append({
                    "field": f"retention.beats.{index}.source_quote",
                    "message": "must be an exact current-chapter excerpt",
                })
            if not beat.event_ids or not set(beat.event_ids) <= event_ids:
                issues.append({
                    "field": f"retention.beats.{index}.event_ids",
                    "message": "must reference diagnosed current-chapter events",
                })
            if set(beat.new_information_fact_ids) - fact_ids:
                issues.append({
                    "field": f"retention.beats.{index}.new_information_fact_ids",
                    "message": "references unknown information fact ids",
                })
        event_beat_positions = {
            event_id: [
                index
                for index, beat in enumerate(showrunner.retention.beats)
                if event_id in beat.event_ids
            ]
            for event_id in event_ids
        }
        for event in diagnosis.events:
            dependent_positions = event_beat_positions.get(
                event.event_id,
                [],
            )
            for cause_id in event.causes:
                cause_positions = event_beat_positions.get(cause_id, [])
                if (
                    dependent_positions
                    and cause_positions
                    and min(dependent_positions) < min(cause_positions)
                    and max(dependent_positions) <= min(cause_positions)
                ):
                    issues.append(
                        {
                            "field": "retention.beats",
                            "message": (
                                f"{event.event_id} appears before its cause "
                                f"{cause_id} and is not replayed afterward"
                            ),
                        }
                    )
        if not showrunner.information_states:
            issues.append({
                "field": "information_states",
                "message": "at least one explicit viewer/character information state is required",
            })
        for index, fact in enumerate(showrunner.information_states):
            # One bundled message per condition group starved the repair loop
            # of signal: the model could not tell which requirement it had
            # missed and repeated the same mistake every revision.
            fact_problems: list[str] = []
            if not grounded(fact.source_quote):
                fact_problems.append(
                    "source_quote is not a verbatim current-chapter excerpt; "
                    "copy the matching diagnosis event's source_quote exactly, "
                    "including punctuation"
                )
            if not set(fact.source_event_ids) <= event_ids:
                fact_problems.append(
                    "source_event_ids reference unknown events: "
                    + ",".join(sorted(set(fact.source_event_ids) - event_ids))
                )
            if (
                allowed_reveal_event_ids is not None
                and not set(fact.source_event_ids) <= allowed_reveal_event_ids
            ):
                fact_problems.append(
                    "source_event_ids are outside the current series projection's "
                    "allowed_reveal_event_ids"
                )
            if fact_problems:
                issues.append({
                    "field": f"information_states.{index}",
                    "message": "; ".join(fact_problems),
                })
            if fact.reveal_beat_id and fact.reveal_beat_id not in beat_ids:
                issues.append({
                    "field": f"information_states.{index}.reveal_beat_id",
                    "message": "references an unknown retention beat",
                })
            unknown = sorted(
                {
                    item.character_name
                    for item in fact.character_awareness
                    if item.character_name not in canonical_names
                }
            )
            if unknown:
                issues.append({
                    "field": f"information_states.{index}.character_awareness",
                    "message": "must use StoryBible character names",
                    "unknown": unknown,
                })
        for index, delta in enumerate(showrunner.character_state_deltas):
            delta_problems: list[str] = []
            if delta.character_name not in canonical_names:
                delta_problems.append(
                    f"character_name '{delta.character_name}' is not a StoryBible "
                    "character; use one of: " + "、".join(sorted(canonical_names))
                )
            if not grounded(delta.source_quote):
                delta_problems.append(
                    "source_quote is not a verbatim current-chapter excerpt; "
                    "copy the matching diagnosis event's source_quote exactly, "
                    "including punctuation"
                )
            if not set(delta.event_ids) <= event_ids:
                delta_problems.append(
                    "event_ids reference unknown events: "
                    + ",".join(sorted(set(delta.event_ids) - event_ids))
                )
            if delta.before == delta.after:
                delta_problems.append("before and after are identical; drop the delta if nothing changed")
            if delta_problems:
                issues.append({
                    "field": f"character_state_deltas.{index}",
                    "message": "; ".join(delta_problems),
                })
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
        if expected_delta_events - covered_delta_events:
            issues.append({
                "field": "character_state_deltas",
                "message": "must cover diagnosed character state changes",
                "missing_event_ids": sorted(expected_delta_events - covered_delta_events),
            })
        if require_episode_intent:
            if showrunner.opposition is None:
                issues.append({
                    "field": "opposition",
                    "message": "v5 Showrunner must name the current opposition, goal, tactic, and source events",
                })
            elif not set(showrunner.opposition.source_event_ids) <= event_ids:
                issues.append({
                    "field": "opposition.source_event_ids",
                    "message": "opposition must use current-projection events only",
                })
            if showrunner.episode_mode == EpisodeMode.CHOICE:
                choice_grounded = bool(
                    showrunner.protagonist_choice
                    and _ground_quote(showrunner.choice_source_quote, episode.source_text)
                    and _quote_key(showrunner.protagonist_choice)
                    in _quote_key(showrunner.choice_source_quote)
                )
                cost_grounded = bool(
                    showrunner.cost_paid
                    and _ground_quote(showrunner.cost_source_quote, episode.source_text)
                    and _quote_key(showrunner.cost_paid)
                    in _quote_key(showrunner.cost_source_quote)
                )
                if not choice_grounded:
                    issues.append({
                        "field": "protagonist_choice",
                        "message": "choice_episode requires a normalized-substring grounded choice",
                    })
                if not cost_grounded:
                    issues.append({
                        "field": "cost_paid",
                        "message": "choice_episode requires a normalized-substring grounded paid cost",
                    })
        if issues:
            raise ValueError(json.dumps({"domain_errors": issues}, ensure_ascii=False))
        return showrunner

    def _plan_showrunner(
        self,
        episode: Episode,
        diagnosis: ChapterDiagnosis,
        bible: StoryBible,
        previous_state: SeriesState | None,
        series_development: SeriesDevelopmentPlan | None = None,
    ) -> ShowrunnerPlan:
        schema = ShowrunnerPlan.model_json_schema()
        system = (
            "你是商业竖屏短剧的Showrunner，只做分镜之前的观众、信息和人物状态决策，不写具体镜头提示词。"
            "只使用当前章与上一集已确认状态，不得引用后文。planning_mode=planner。"
            "必须服从输入的系列压力引擎与当前章projection；输入不会提供其他章projection，"
            "不得自行补写未来事实。information_states只能使用projection.allowed_reveal_event_ids。"
            "明确选择episode_mode：如果当前章证据支持主角有代价的决定，用choice_episode并填写"
            "protagonist_choice/choice_source_quote/cost_paid/cost_source_quote，choice和cost都按"
            "归一化子串落在各自source_quote；答不出来就用pressure_episode，靠信息与对手压力推进。"
            "两种模式都必须填写结构化opposition（具名对手或明确压力主体、目标、手段、当前章事件）。"
            "retention使用4-8个0-1相对时间节点，包含前5%的hook、question、至少一次payoff或reversal、"
            "后20%的cliffhanger。节点的target_start_ratio必须大致均匀铺满整条0到1："
            "把0、各节点起始位置、1排成一列，任意相邻两个数之差都不得超过"
            "max_attention_gap_ratio（默认0.25）——注意0到第一个节点、"
            "最后一个节点到1这两段也要算。例如6个节点可取0.0、0.18、0.36、0.54、0.7、0.85；"
            "如果cold open先展示失败、受辱、失去能力或其他结果，前20%内必须用真实event_ids同时建立"
            "对应的before状态、失去的能力/机制为何重要，以及观众应追问的核心因果；"
            "这些前置条件必须绑定到target_start_ratio<=0.20的前两个beat，不能只写在audience_question或promise里。"
            "可以暂时保留最终答案，但不能把理解结果所需的基本概念和落差拖到35%以后。"
            "配角测验、对照组或其他secondary branch只能在主角核心问题已经通过event_ids建立后开始。"
            "人物做出逆人群、跨阶层、追随或公开站队等高代价行动时，关系证据和行动动机必须放在"
            "更早的beat，行动之后的对白不能反向补足此前尚未成立的动机。"
            "不要把节点挤在开头和结尾而让中段留出大空窗。此阶段还没有镜头编号，"
            "shot_indexes必须留空；每个节点只绑定event_ids、逐字source_quote、观众问题、承诺、新信息和情绪变化。"
            "information_states逐条记录事实真假、观众认知、各角色认知或误解、信息差用途和揭示节点。"
            "character_state_deltas只记录当前章确实改变的社会地位、关系、力量、情绪、信心或服装；"
            "before与after不得相同，永久脸型发型不属于剧情状态。所有事实必须有当前章逐字证据。"
            "所有source_quote一律从章节诊断中对应event的source_quote整段逐字复制（包含全部标点和引号），"
            "不要自己重新摘抄、缩写或改写原文——校验按逐字匹配执行，改一个标点都会失败。"
            "character_name和character_awareness必须逐字使用故事圣经characters里的角色名，不得用简称或别名。"
            "以下字段只能从给定取值里选，不得自造，也不要翻译成中文："
            "retention.beats.function取hook、question、pressure、escalation、payoff、reversal、cliffhanger之一；"
            "information_states.truth_status取confirmed、potential、misread之一；"
            "information_states.viewer_awareness和character_awareness.awareness取"
            "knows、suspects、misled、unaware之一；"
            "information_states.dramatic_use取viewer_leads（观众先知道）、character_leads（角色先知道）、"
            "simultaneous_reveal（同步揭示）、misunderstanding（误会）、withheld（暂时隐瞒）之一。"
            "严格输出JSON。"
        )
        user = (
            f"当前集：{episode.index} {episode.source_title}\n"
            f"章节诊断：{diagnosis.model_dump_json()}\n"
            f"上一集状态：{previous_state.model_dump_json() if previous_state else '{}'}\n"
            f"系列引擎与当前章投影：{json.dumps(_current_projection_context(series_development, episode.index), ensure_ascii=False) if series_development is not None else '{}'}\n"
            f"故事圣经：{bible.model_dump_json()}\n当前章原文：{episode.source_text}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        return _bounded_validate(
            "plan_showrunner",
            self.settings.planner_max_revisions,
            lambda repair: self._json(
                system, user, repair, token_budget=SHOWRUNNER_TOKEN_BUDGET
            ),
            lambda data: self._validate_showrunner_data(
                data,
                episode,
                diagnosis,
                bible,
                (
                    set(
                        series_development.chapter_projections[
                            episode.index - 1
                        ].allowed_reveal_event_ids
                    )
                    if series_development is not None
                    else None
                ),
                series_development is not None,
            ),
        )

    def plan_episode(self, novel: NovelDocument, episode: Episode, bible: StoryBible) -> EpisodePlan:
        schema = EpisodePlan.model_json_schema()
        source_chars = len(re.sub(r"\s+", "", episode.source_text))
        if source_chars <= 1200:
            size_guidance = "全片旁白与对白合计450-750个汉字、8-14个镜头"
        elif source_chars <= 3000:
            size_guidance = "全片旁白与对白合计700-1100个汉字、16-24个镜头"
        else:
            size_guidance = "全片旁白与对白合计900-1400个汉字、24-36个镜头"
        system = (
            "你是小说改编漫剧编剧。忠于原文人物关系、关键事件、顺序、因果和结局；不得新增核心情节。"
            f"不使用静态片头，0-3秒直接进入当前章有原文依据的冲突或悬念。原文有效字数约{source_chars}，{size_guidance}；"
            "不得为凑镜头或字数重复情节、虚构事件或拆碎同一句话。"
            "每个镜头设置 turns：旁白 role=narrator、speaking=false；人物对白 role 和 speaker_name 均使用角色原名、"
            "speaking=true、delivery_mode=visible_dialogue；内心声或画外对白使用角色原名、"
            "speaking=false，并分别设置delivery_mode=inner_voice或offscreen_dialogue。"
            "保留的原文引号台词必须归给具体角色，不得标成旁白，并设置derivation=verbatim逐字引用；"
            "承载因果、来历或转折的叙述段落设置derivation=derived，改写成角色真会说出口的话或可拍摄的反应，"
            "并在source_quote里引用它依据的那段叙述。"
            "不得把需要保留的冲突对白概括成一句旁白；可删或压缩不改变局面的重复寒暄和同义嘲讽，"
            "逐字原文turn占比不得超过35%，事实与因果由event_ids和source trace保证。"
            "示范：原文叙述“三年之前，这名声望达到巅峰的天才少年，却是突兀地接受到了有生以来最残酷的打击，"
            "不仅辛辛苦苦修炼十数载方才凝聚的气旋，一夜之间，化为乌有”——"
            "错误做法是写成一句旁白“三年前他的气旋一夜消失”；"
            "正确做法是拆成在场两个角色的一问一答，每个turn都是角色对白、derivation=derived、"
            "source_quote引用这段叙述：甲“他以前，真有传的那么厉害？”／"
            "乙“十一岁凝聚气旋，家族百年之内最年轻。”／甲“那后来呢？”／"
            "乙“三年前，一夜之间，全没了。”／甲“为什么？”／乙“没人知道。”。"
            "悬念要让角色问出来，不能由旁白直接告诉观众。"
            "全片至少三分之一的台词字数应来自derived角色对白；只把derived用在旁白上等于没有改编。"
            "一个turn是一口气可自然说完的完整语义句，目标不超过14字，硬上限20字；"
            "不得为了字幕长度拆碎句子。字幕在音频对齐后独立分页，每页最多两行且仍逐字来自turn.text。"
            "一个shot通常承载1-3个语义turn，但同一shot里的可见对白必须属于同一个说话者和同一种delivery_mode；"
            "说话者变化、可见对白切到内心声/画外声、或需要独立反应时必须新建shot。"
            "短剧节奏来自信息、动作和情绪变化，不来自机械断句；同一角色连续发言仍保持对话轴，"
            "但应在胸像、较紧肩部近景和较宽腰上景之间有动机地变化，不得连续复制同一完整构图。"
            "每3-4个对话镜头根据原文需要安排一次建立镜、无声反应、道具插入或环境响应，"
            "用视线匹配、动作接点或声音桥连接，不能为了丰富画面虚构事件。"
            "抽象情绪必须转成不超过三个可见信号，例如停顿、视线、下颌、眉间、呼吸、重心或手部接触；"
            "不要只写‘愤怒、震惊、屈辱’，也不要每句自动加通用手势。"
            "scene_job不能全写‘推进’，应按场次实际作用使用建立、对峙、揭示、反转、决定或收束。"
            "每镜change必须说明镜末新增的信息、关系变化或动作结果；change为空的镜头必须删除或合并。"
            "涉及碎裂、撞击、奔跑、战气或强视效时，动作按准备→发力→接触→反作用→落定组织，"
            "并让衣摆、发梢、轻尘、道具或场景产生少量同方向反馈；时长不足就拆镜。"
            "每个镜头必须填写 performance_plan：动作起点、1-4个有触发和反应的 motion_beats、动作终点；"
            "必须填写camera_plan.mode、motivation、action_axis和screen_direction。默认mode=locked，"
            "由人物表演承担动态；只有人物明确位移、信息揭示或情绪/权力转折才使用motivated_subtle，"
            "章节高潮或关键反转才少量使用motivated_emphasis。每镜最多一条短轨迹，完成后停住；"
            "同场对话始终在行动轴同侧，人物左右和视线方向不得无故交换。"
            "参考图只锁人物身份、服装、环境和画风，不能锁静态姿势、构图或机位。"
            "运行时关键帧只使用当前角色资产和当前场景资产共同控制二维画法，不额外依赖独立风格模板。"
            "覆盖本章开端、发展、高潮和结尾。source_quote 必须逐字摘自本章。画面健康克制，无色情、政治和血腥。"
            "严格输出 JSON。"
        )
        user = (
            f"小说：{novel.title}\n本集：{episode.source_title}\n故事圣经：{bible.model_dump_json()}\n"
            f"原文：{episode.source_text}\nJSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        plan = _bounded_validate(
            "plan_episode",
            self.settings.planner_max_revisions,
            lambda repair: self._json(system, user, repair),
            lambda data: self._validate_episode_data(data, episode, bible),
        )
        return apply_creative_direction(
            plan,
            deterministic_chapter_diagnosis(episode),
            bible,
            profile=self.settings.creative_profile,
        )

    def _diagnose_episode(
        self,
        episode: Episode,
        bible: StoryBible,
        previous_state: SeriesState | None,
    ) -> ChapterDiagnosis:
        schema = ChapterDiagnosis.model_json_schema()
        evidence_bank = "\n".join(
            f"E{index:03d}\t{row}"
            for index, row in enumerate(source_evidence_units(episode.source_text), 1)
        )
        system = (
            "你是逐章漫剧改编的事实编辑。只分析当前章节，不得推测或使用后续章节。"
            "把章节提炼为按原文顺序排列的关键事件表，关键事件必须覆盖开端、人物建立、"
            "冲突发展、因果转折、高潮和章末结果。每个事件引用当前章的精确原文，"
            "hook_source_quote和每个event.source_quote都必须从SOURCE_EVIDENCE中选择一整行逐字复制，"
            "不得概括、缩写、拼接或修改标点；description才用于概括事件。"
            "每条source_quote硬上限500字符，而SOURCE_EVIDENCE中每一行都远短于此，"
            "所以一旦超长就说明你拼接了多行——只复制其中一行。"
            "causes只能引用更早的事件。未知用途细节标为potential_foreshadowing，不得擅自删除。"
            "event.characters只能填写故事圣经characters里的具名角色原名；"
            "人群、众人、族人这类无名群体不是具名角色，不要写进characters，"
            "他们的言行写进description即可。"
            "previous_state只用于连续性，不得当作本集新增剧情。严格输出JSON。"
        )
        user = (
            f"当前章节：{episode.source_title}\n当前章原文：{episode.source_text}\n"
            f"SOURCE_EVIDENCE（source_quote只能逐字复制其中一整行）：\n{evidence_bank}\n"
            f"系列设定：{bible.model_dump_json()}\n"
            f"上一集状态：{previous_state.model_dump_json() if previous_state else '{}'}\n"
            "事件数量按语义决定，通常12-30个；不要把每一句描写都机械列成事件。"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        return _bounded_validate(
            "diagnose_episode",
            self.settings.planner_max_revisions,
            lambda repair: self._json(
                system, user, repair, token_budget=DIAGNOSIS_TOKEN_BUDGET
            ),
            lambda data: validate_chapter_diagnosis(
                ChapterDiagnosis.model_validate(data), episode, bible
            ),
        )

    def _review_episode(
        self,
        episode: Episode,
        diagnosis: ChapterDiagnosis,
        plan: EpisodePlan,
        previous_state: SeriesState | None,
    ) -> ScriptQualityReport:
        schema = ScriptQualityReport.model_json_schema()
        system = (
            "你是独立的漫剧剧本审稿人，不负责美化分镜。检查剧本是否忠于当前章、"
            "是否先铺垫再兑现、主要人物是否在承担冲突前完成身份和立场建立、"
            "结尾是否停在当前章边界，以及是否使用后文剧情。"
            "short-drama-adaptive-v1允许0-3秒展示当前章内的结果、公开受压或关系异常作为冷开场，"
            "但cold_open_source_quote必须来自当前章，前两镜必须实际呈现，随后必须补足原因并在正常因果位置再次兑现；"
            "不得借用后文章节的答案。"
            "请站在从未读过原著的观众角度，确认能回答：主要人物是谁、人物关系是什么、"
            "发生了什么、为什么发生、造成什么后果、人物为何这样反应。"
            "检查旁白是否超过dramaturgy.narration_budget_ratio；能用动作、对白、反应、道具或环境结果表达的信息，"
            "不得继续写成解释性旁白。"
            "检查showrunner_plan：留存节点从hook、question、escalation到payoff/reversal和cliffhanger连续分布，"
            "相邻节点不得留下超过retention.max_attention_gap_ratio的中段空窗；每个information_state必须回答"
            "观众知道什么、角色知道或误解什么，并有当前章逐字证据；character_state_delta只记录当前章真正改变的"
            "社会地位、关系、力量、情绪、信心或服装状态，不得把永久长相当作剧情状态。"
            "检查每镜shot_intent是否绑定留存节点、观众焦点和信息事实；audio_beats必须由台词、动作、揭示或反应触发，"
            "不得机械地每隔几秒堆冲击音。"
            "每个turn应是一口气可自然说完、只承载一个核心事实、动作或反应的完整语义句；目标不超过14字，"
            "超过20字即因表演与镜头时长风险判为blocking。不得为了两行字幕把一句话切碎。"
            "长镜头可以连续承载多个语义turn，不能因拆台词而要求增加切镜。"
            "逐条审查derivation=derived的turn并做删除测试：删掉它以后，必须能明确指出哪个当前章event/fact"
            "失去可拍载体，或哪条已存在因果会断；答案必须与turn.serves一致。答不出来就是闲聊，"
            "标记blocking并要求删除。允许的翻译型扩写包括：无名听者承载叙述、演出角色如何获知既有事实、"
            "演出既有代价落点、补具名对手或围观者反应。禁止新增information_state事实、原文事件、"
            "StoryBible之外的具名角色和原创支线；未由episode_contract指定的后文伏笔也禁止。"
            "只要存在缺失关键因果、突兀结论、人物动机不明、提前剧透或未来剧情，"
            "passed必须为false并给blocking issue。"
            "计数值可按输入填写，程序会重新计算。严格输出JSON。"
        )
        user = (
            f"当前章原文：{episode.source_text}\n"
            f"章节诊断：{diagnosis.model_dump_json()}\n"
            f"上一集状态：{previous_state.model_dump_json() if previous_state else '{}'}\n"
            f"待审剧本：{plan.model_dump_json()}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        qualitative = _bounded_validate(
            "review_episode",
            self.settings.planner_max_revisions,
            lambda repair: self._json(
                system, user, repair, token_budget=REVIEW_TOKEN_BUDGET
            ),
            ScriptQualityReport.model_validate,
        )
        return evaluate_script_quality(
            plan,
            diagnosis,
            episode,
            qualitative=qualitative,
            previous_state=previous_state,
        )

    def _expand_script_turns(
        self,
        episode: Episode,
        bible: StoryBible,
        diagnosis: ChapterDiagnosis,
        plan: EpisodePlan,
        required_chars: int,
        previous_state: SeriesState | None,
    ) -> EpisodePlan:
        schema = ScriptExpansion.model_json_schema()
        current_chars = sum(
            len(re.sub(r"\s+", "", turn.text))
            for shot in plan.shots
            for turn in shot.turns
        )
        compact_shots = [
            {
                "shot_index": shot.index,
                "event_ids": shot.event_ids,
                "characters": shot.characters,
                "source_quote": shot.source_quote,
                "turns": [turn.model_dump(mode="json") for turn in shot.turns],
            }
            for shot in plan.shots
        ]
        system = (
            "你是漫剧台词编辑。只补写现有镜头的turns，不得修改镜头顺序、事件、人物关系或结局。"
            "输入中的现有turn是事实与角色归属基线；若过长、重复、书面化或导致逐字比例超限，"
            "可在不改变事实的前提下删除非必要turn，或改用当前叙述证据外化成短句。"
            "原文带引号的短台词归具体角色并设置derivation=verbatim逐字引用，不得标成旁白；"
            "长对白可设置derivation=abridged，只按原顺序删除完整标点子句，不得改词；"
            "保留的长子句允许拆成多个连续turn，每个turn不超过20字，连续拼接后必须等于所选完整子句。"
            "叙述段落设置derivation=derived，改写成角色对白或可拍摄的反应，并引用所依据的叙述句；"
            "derived必须声明device枚举，并用serves指向承载的现有event_id/fact_id；"
            "可以用无名听者、桥接获取过程和反应来翻译原文，但不得引入原文没有的事实、事件或具名角色。"
            "优先补原文已有的短对白、内心声或必要因果，不得用摘要旁白填充字数；"
            "能由visual_prompt和performance_plan表演的信息不要重复朗读。"
            "每个turn只讲一个核心事实、动作或反应，同时必须保持自然完整的语义与呼吸，目标不超过14字，硬上限20字。"
            "不得为字幕分页切碎完整句；字幕由音频对齐层另行分页。"
            "同一shot通常用1-3个语义turn承载一个连续表演beat，必要时可更多，但不要增加shot或切镜。"
            "每个shot总turns.text按实际动作时长决定，不为字数填充。只返回需要替换的shot_index和完整turns数组。"
            "严格输出JSON。"
        )
        user = (
            f"当前有效字数：{current_chars}；最低目标：{required_chars}；建议目标："
            f"{required_chars + 100}。\n当前章原文：{episode.source_text}\n"
            f"角色标准名：{json.dumps([item.name for item in bible.characters], ensure_ascii=False)}\n"
            f"待补写镜头：{json.dumps(compact_shots, ensure_ascii=False)}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        shots_by_index = {shot.index: shot for shot in plan.shots}
        fidelity_codes = {
            "narrator_speaks_character_line",
            "verbatim_turn_not_quoted",
            "turn_text_contains_stage_direction",
            "derived_turn_narrates_self",
            "derived_turn_not_rewritten",
            "derived_turn_paraphrases_dialogue",
        }

        def validate_expansion(data: dict) -> EpisodePlan:
            expansion = ScriptExpansion.model_validate(data)
            patches = {patch.shot_index: patch.turns for patch in expansion.shots}
            unknown = sorted(set(patches) - set(shots_by_index))
            if unknown:
                raise ValueError(f"script expansion uses unknown shot indexes: {unknown}")
            changed_existing = []
            for shot_index, turns in patches.items():
                existing = shots_by_index[shot_index].turns
                preserves_prefix = (
                    len(turns) >= len(existing)
                    and turns[: len(existing)] == existing
                )
                exact_source_restoration = bool(turns) and all(
                    turn.derivation == TurnDerivation.VERBATIM
                    and len(_quote_key(turn.text)) >= 5
                    and _quote_key(turn.text) in _quote_key(turn.source_quote)
                    and re.sub(r"\s+", "", turn.source_quote)
                    in re.sub(r"\s+", "", episode.source_text)
                    for turn in turns
                ) and all(
                    max(
                        (
                            SequenceMatcher(
                                None,
                                _quote_key(original.text),
                                _quote_key(candidate.text),
                            ).ratio()
                            for candidate in turns
                        ),
                        default=0.0,
                    )
                    >= 0.60
                    for original in existing
                )
                if not preserves_prefix and not exact_source_restoration:
                    changed_existing.append(shot_index)
            if changed_existing:
                raise ValueError(
                    "script expansion must preserve every existing turn exactly and only append; "
                    f"changed shot indexes: {changed_existing}"
                )
            expanded = plan.model_copy(
                update={
                    "shots": [
                        shot.model_copy(
                            update={"turns": patches.get(shot.index, shot.turns)}
                        )
                        for shot in plan.shots
                    ]
                }
            )
            expanded = self._validate_episode_data(
                expanded.model_dump(mode="json"), episode, bible
            )
            report = evaluate_script_quality(
                expanded,
                diagnosis,
                episode,
                previous_state=previous_state,
            )
            fidelity_issues = [
                issue for issue in report.issues if issue.code in fidelity_codes
            ]
            if fidelity_issues:
                raise ValueError(
                    json.dumps(
                        {
                            "expansion_fidelity_issues": [
                                issue.model_dump(mode="json") for issue in fidelity_issues
                            ]
                        },
                        ensure_ascii=False,
                    )
                )
            expanded_chars = sum(
                len(re.sub(r"\s+", "", turn.text))
                for shot in expanded.shots
                for turn in shot.turns
            )
            if expanded_chars < required_chars:
                raise ValueError(
                    "script expansion remains too short: "
                    f"{expanded_chars} chars, requires at least {required_chars}"
                )
            return expanded

        return _bounded_validate(
            "expand_script_turns",
            2,
            lambda repair: self._json(
                system,
                user,
                repair,
                token_budget=SCRIPT_EXPANSION_TOKEN_BUDGET,
            ),
            validate_expansion,
        )

    def _repair_turn_attribution(
        self,
        episode: Episode,
        bible: StoryBible,
        diagnosis: ChapterDiagnosis,
        plan: EpisodePlan,
        report: ScriptQualityReport,
        previous_state: SeriesState | None,
    ) -> EpisodePlan:
        target_shots = sorted(
            {
                shot_index
                for issue in report.issues
                if issue.severity == "blocking"
                and issue.code in DIALOGUE_ATTRIBUTION_CODES
                for shot_index in issue.shot_indexes
            }
        )
        if not target_shots:
            raise ValueError("turn attribution repair has no target shots")
        shots_by_index = {shot.index: shot for shot in plan.shots}
        missing_plan_shots = sorted(set(target_shots) - set(shots_by_index))
        if missing_plan_shots:
            raise ValueError(
                f"turn attribution repair references missing shots: {missing_plan_shots}"
            )
        compact_shots = [
            {
                "shot_index": shot_index,
                "event_ids": shots_by_index[shot_index].event_ids,
                "characters": shots_by_index[shot_index].characters,
                "location": shots_by_index[shot_index].location,
                "shot_source_quote": shots_by_index[shot_index].source_quote,
                "turns": [
                    turn.model_dump(mode="json")
                    for turn in shots_by_index[shot_index].turns
                ],
            }
            for shot_index in target_shots
        ]
        target_issues = [
            issue.model_dump(mode="json")
            for issue in report.issues
            if issue.severity == "blocking"
            and issue.code in ATTRIBUTION_REPAIR_CODES
        ]
        schema = ScriptExpansion.model_json_schema()
        system = (
            "你是漫剧对白归属修复器，只修输入列出的镜头turns，不得输出或修改其他镜头。"
            "每个target shot必须返回一次，并给出该镜头完整的turns数组。"
            "原文引号内的每条对白必须逐字完整保留，设置derivation=verbatim；不得删词、合并或改写。"
            "明确说话人使用故事圣经标准角色名；群体匿名嘲讽可使用role=人群、speaker_name=人群、"
            "speaking=false、delivery_mode=offscreen_dialogue；测验结果等现场宣读使用在场测验员。"
            "旁白只能承载原文没有说话人的叙述，role=narrator、speaker_name=旁白、"
            "speaking=false、delivery_mode=narration、derivation=derived。"
            "不得新增事实，不得更改event_ids、镜头顺序、动作、地点或Showrunner计划。严格输出JSON。"
        )
        user = (
            f"目标镜头：{json.dumps(target_shots, ensure_ascii=False)}\n"
            f"门禁错误：{json.dumps(target_issues, ensure_ascii=False)}\n"
            f"故事圣经角色名：{json.dumps([item.name for item in bible.characters], ensure_ascii=False)}\n"
            f"待修镜头：{json.dumps(compact_shots, ensure_ascii=False)}\n"
            f"当前章原文：{episode.source_text}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )

        def validate_patch(data: dict) -> EpisodePlan:
            patch = ScriptExpansion.model_validate(data)
            patches = {item.shot_index: item.turns for item in patch.shots}
            supplied = set(patches)
            expected = set(target_shots)
            if supplied != expected:
                raise ValueError(
                    "turn attribution patch must cover exactly the target shots; "
                    f"missing={sorted(expected - supplied)}, "
                    f"unexpected={sorted(supplied - expected)}"
                )
            candidate = plan.model_copy(
                update={
                    "shots": [
                        shot.model_copy(
                            update={"turns": patches.get(shot.index, shot.turns)}
                        )
                        for shot in plan.shots
                    ]
                }
            )
            candidate = self._validate_episode_data(
                candidate.model_dump(mode="json"), episode, bible
            )
            candidate_report = evaluate_script_quality(
                candidate,
                diagnosis,
                episode,
                previous_state=previous_state,
            )
            if not candidate_report.passed:
                raise ValueError(candidate_report.model_dump_json())
            return candidate

        return _bounded_validate(
            "repair_turn_attribution",
            2,
            lambda repair: self._json(
                system,
                user,
                repair,
                token_budget=TURN_ATTRIBUTION_TOKEN_BUDGET,
            ),
            validate_patch,
        )

    def _repair_review_content(
        self,
        episode: Episode,
        bible: StoryBible,
        diagnosis: ChapterDiagnosis,
        plan: EpisodePlan,
        report: ScriptQualityReport,
        previous_state: SeriesState | None,
    ) -> EpisodePlan:
        blocking_issues = [
            issue for issue in report.issues if issue.severity == "blocking"
        ]
        if not blocking_issues or not all(issue.shot_indexes for issue in blocking_issues):
            raise ValueError("content patch requires blocking issues with target shots")
        shots_by_index = {shot.index: shot for shot in plan.shots}
        target_set = {
            index for issue in blocking_issues for index in issue.shot_indexes
        }
        for issue in blocking_issues:
            if "CAUS" not in issue.code.upper():
                continue
            reveal_index = max(issue.shot_indexes)
            target_set.update(
                index
                for index in (reveal_index - 2, reveal_index - 1)
                if index in shots_by_index
            )
        target_shots = sorted(target_set)
        if set(target_shots) - set(shots_by_index):
            raise ValueError("content patch references unknown target shots")
        compact_shots = [
            {
                "shot_index": index,
                "event_ids": shots_by_index[index].event_ids,
                "location": shots_by_index[index].location,
                "characters": shots_by_index[index].characters,
                "source_quote": shots_by_index[index].source_quote,
                "turns": [
                    turn.model_dump(mode="json")
                    for turn in shots_by_index[index].turns
                ],
                "visual_prompt": shots_by_index[index].visual_prompt,
                "motion_prompt": shots_by_index[index].motion_prompt,
                "performance_plan": (
                    shots_by_index[index].performance_plan.model_dump(mode="json")
                    if shots_by_index[index].performance_plan
                    else None
                ),
            }
            for index in target_shots
        ]
        schema = ScriptContentPatch.model_json_schema()
        system = (
            "你是漫剧镜头内容修复器，只修独立审稿点名镜头的少数字段。"
            "非因果blocking issue至少修改它列出的一个shot_index；因果问题必须修改结果镜头之前提供的目标窗口。"
            "不得输出目标集合之外的镜头。"
            "每个patch只允许shot_index以及turns、visual_prompt、motion_prompt、performance_plan、change、shot_intent。"
            "不得修改event_ids、镜头顺序、地点、人物资产、相机、音频、Showrunner或章节事实。"
            "因果上下文缺失时，在揭示镜头之前的目标镜头加入当前章有证据的before状态或关系信息；"
            "‘昔日天才’、‘曾经很强’这类抽象标签不算完整铺垫：必须从当前章原文选择具体年龄、等级、"
            "能力机制、排名或可观察成就，说明失去的东西为何重要，并放在结果揭示之前。"
            "需要保留的原文对白使用逐字verbatim；重复或不改变局面的原句可删，叙述外化才可derived，"
            "修复后逐字turn占比不得超过35%。"
            "人物动机不清时，把选择过程写成可见动作：触发、犹豫或察觉、主要动作、他人反应和结果，"
            "如果反馈提到过去关系或熟悉程度，必须用当前章证据写清before关系，再表现人物当前主动选择，"
            "不能只写现在的态度。"
            "不得新增原文没有的行为结果。返回最小patch JSON，不解释。"
        )
        user = (
            f"blocking issues：{json.dumps([i.model_dump(mode='json') for i in blocking_issues], ensure_ascii=False)}\n"
            f"目标镜头：{json.dumps(target_shots, ensure_ascii=False)}\n"
            f"故事圣经角色：{json.dumps([item.name for item in bible.characters], ensure_ascii=False)}\n"
            f"待修镜头：{json.dumps(compact_shots, ensure_ascii=False)}\n"
            f"当前章原文：{episode.source_text}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        def validate_patch(data: dict) -> EpisodePlan:
            patch = ScriptContentPatch.model_validate(data)
            patches = {item.shot_index: item for item in patch.shots}
            supplied = set(patches)
            unexpected = sorted(supplied - set(target_shots))
            if unexpected:
                raise ValueError(
                    f"content patch uses non-target shot indexes: {unexpected}"
                )
            uncovered = [
                issue.shot_indexes
                for issue in blocking_issues
                if "CAUS" not in issue.code.upper()
                and not set(issue.shot_indexes) & supplied
            ]
            if uncovered:
                raise ValueError(
                    f"content patch leaves blocking issues uncovered: {uncovered}"
                )
            causal_order_misses = [
                issue.shot_indexes
                for issue in blocking_issues
                if "CAUS" in issue.code.upper()
                and not any(
                    shot_index < max(issue.shot_indexes)
                    for shot_index in supplied
                )
            ]
            if causal_order_misses:
                raise ValueError(
                    "causal context patch must edit an earlier shot than the reveal; "
                    f"issue targets: {causal_order_misses}"
                )
            concrete_pattern = re.compile(
                r"(?:\d+岁|[一二三四五六七八九十百]+岁|气旋|百年|最年轻|"
                r"第一|巅峰|排名|曾经.{0,20}(?:段|等级|能力|战者))"
            )
            causal_detail_misses = []
            for issue in blocking_issues:
                if "CAUS" not in issue.code.upper():
                    continue
                reveal_index = max(issue.shot_indexes)
                earlier_text = "".join(
                    json.dumps(
                        {
                            "turns": (
                                [
                                    turn.model_dump(mode="json")
                                    for turn in patches[index].turns
                                ]
                                if patches[index].turns is not None
                                else None
                            ),
                            "visual_prompt": patches[index].visual_prompt,
                            "motion_prompt": patches[index].motion_prompt,
                            "performance_plan": (
                                patches[index].performance_plan.model_dump(mode="json")
                                if patches[index].performance_plan is not None
                                else None
                            ),
                            "change": patches[index].change,
                            "shot_intent": (
                                patches[index].shot_intent.model_dump(mode="json")
                                if patches[index].shot_intent is not None
                                else None
                            ),
                        },
                        ensure_ascii=False,
                    )
                    for index in supplied
                    if index < reveal_index
                )
                if not concrete_pattern.search(earlier_text):
                    causal_detail_misses.append(issue.shot_indexes)
            if causal_detail_misses:
                raise ValueError(
                    "causal context patch must add concrete age, level, mechanism, "
                    "rank or achievement evidence in an earlier shot; "
                    f"issue targets: {causal_detail_misses}"
                )
            relationship_pattern = re.compile(
                r"(?:以前|曾经|当年|过去|原本|从前|仰慕|熟悉|旧日)"
            )
            relationship_misses = []
            for issue in blocking_issues:
                if "MOTIVATION" not in issue.code.upper() or not re.search(
                    r"(?:过去|曾经|关系|熟悉|仰慕)", issue.message
                ):
                    continue
                issue_text = "".join(
                    json.dumps(
                        patches[index].model_dump(mode="json", exclude={"shot_index"}),
                        ensure_ascii=False,
                    )
                    for index in supplied & set(issue.shot_indexes)
                )
                if not relationship_pattern.search(issue_text):
                    relationship_misses.append(issue.shot_indexes)
            if relationship_misses:
                raise ValueError(
                    "motivation patch must include current-chapter evidence of the "
                    "past relationship before the present choice; "
                    f"issue targets: {relationship_misses}"
                )
            shots = []
            for shot in plan.shots:
                item = patches.get(shot.index)
                if item is None:
                    shots.append(shot)
                    continue
                updates = {
                    field: value
                    for field, value in (
                        ("turns", item.turns),
                        ("visual_prompt", item.visual_prompt),
                        ("motion_prompt", item.motion_prompt),
                        ("performance_plan", item.performance_plan),
                        ("change", item.change),
                        ("shot_intent", item.shot_intent),
                    )
                    if value is not None
                }
                shots.append(shot.model_copy(update=updates))
            candidate = plan.model_copy(update={"shots": shots})
            candidate = self._validate_episode_data(
                candidate.model_dump(mode="json"), episode, bible
            )
            candidate_report = evaluate_script_quality(
                candidate,
                diagnosis,
                episode,
                previous_state=previous_state,
            )
            if not candidate_report.passed:
                raise ValueError(candidate_report.model_dump_json())
            return candidate

        return _bounded_validate(
            "repair_review_content",
            2,
            lambda repair: self._json(
                system,
                user,
                repair,
                token_budget=CONTENT_PATCH_TOKEN_BUDGET,
            ),
            validate_patch,
        )

    def _update_series_state(
        self,
        episode: Episode,
        bible: StoryBible,
        diagnosis: ChapterDiagnosis,
        plan: EpisodePlan,
        previous_state: SeriesState | None,
    ) -> SeriesState:
        schema = SeriesState.model_json_schema()
        system = (
            "你是连续剧状态管理员。根据当前章已经发生的事实更新完整series_state快照。"
            "新事实必须附当前章精确原文和当前集编号；历史事实必须原样继承上一状态，"
            "不得把推测写成confirmed，不得写入后文秘密。服装、位置、伤势、知识、关系、"
            "社会地位、力量、信心、道具和未解悬念只在当前章有依据时改变。"
            "将showrunner_plan.character_state_deltas提交到对应characters状态，将当前集information_states"
            "转为带source_episode证据的跨集information_states；同一事实后续只更新知情者或认知状态，不重复造事实。"
            "严格输出JSON。"
        )
        user = (
            f"当前集编号：{episode.index}\n当前章节：{episode.source_title}\n"
            f"当前章原文：{episode.source_text}\n系列设定：{bible.model_dump_json()}\n"
            f"上一状态：{previous_state.model_dump_json() if previous_state else '{}'}\n"
            f"章节诊断：{diagnosis.model_dump_json()}\n已审核剧本：{plan.model_dump_json()}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        return _bounded_validate(
            "update_series_state",
            self.settings.planner_max_revisions,
            lambda repair: self._json(
                system, user, repair, token_budget=SERIES_STATE_TOKEN_BUDGET
            ),
            lambda data: validate_series_state(
                SeriesState.model_validate(data), episode, previous_state
            ),
        )

    def _plan_retention_beat_script(
        self,
        *,
        episode: Episode,
        diagnosis: ChapterDiagnosis,
        bible: StoryBible,
        beat: RetentionBeat,
        previous_close_state: str,
        released_fact_ids: list[str],
        beat_root: Path,
        episode_contract: EpisodeContract | None = None,
    ) -> RetentionBeatScript:
        native_dialogue = self.settings.final_audio_policy == NATIVE_DIALOGUE_POLICY
        events = [
            event
            for event in diagnosis.events
            if event.event_id in beat.event_ids
        ]
        system = (
            "你只负责一个RetentionBeat的剧本写作，不负责表演、机位、声音或图像prompt。"
            "按当前beat实际需要输出1-6个BeatScriptShot；镜数不是门，不能为凑数制造闲聊。"
            "每镜只写人物/地点、可见blocking、这一镜改变了什么、"
            "source_quote、event_ids、shot_intent和不可变turns。每个turn是一口气自然说完的短句，"
            "不得拆词凑数，不得借用其他beat或后文事实。可以发明无名听者与台词，把已有叙述事实"
            "翻译成有听者的一问一答；可以演出角色如何知道原文已宣布的事实、代价落到谁身上，"
            "也可以给具名对手或围观者补反应。无名载体必须用offscreen_dialogue，不得冒充新具名角色。"
            "纯动作/反应镜不要写旁白：使用role=action、speaker_name=动作、speaking=false、"
            "delivery_mode=silent_action，text只写可见大动作，并照常填写derivation/device/serves；"
            "绝不能发明information_state里没有的事实、原文没有的新事件或StoryBible之外的具名角色；"
            "原创支线禁止。episode contract没有指定setup_obligation时不得埋后文伏笔；"
            "纯节奏爽点每个beat最多一个且不能改变事实。每条derived必须填写serves，指向它承载的"
            "当前event_id或已登记fact_id；删掉后答不出哪个原文事实失去载体、哪条因果会断，就删掉。"
            "每个shot/turn的source_quote必须逐字复制当前章中的一条连续原文证据，不能把相隔的两段"
            "拼成一个quote；需要两段证据就拆shot或让各turn分别引用自己的连续原文。"
            "第一镜承接incoming_close_state，"
            "最后一镜必须兑现本beat的promise并形成可交接close_state。"
            "原文长对白可用derivation=abridged：只允许按顺序删除完整标点子句；"
            "它只适用于source_quote中恰好一条带引号对白，不能拿无引号叙述冒充abridged；"
            "保留的长子句可拆成连续turn，拼接后必须等于所选完整子句。"
            "所有发声turn硬上限20字；原文长台词不能继续标成单个verbatim，必须按上述abridged规则"
            "删完整子句或把一个保留长子句拆成连续短turn。原文内心声在native_dialogue下不能改标签成"
            "narration/inner_voice：若该事实必要，就用无名听者、已知角色对白或silent_action外化并写serves；"
            "若同一事实已由可见动作承载，就删除该发声turn。"
            "derivation=derived必须从listener_qa、crowd_proxy、half_line、evidence_object、"
            "spatial、consequence、inner_voice、narration中声明device。"
            "前史叙述外化示例（内容必须换成当前beat证据）：第一镜无名姐妹用offscreen_dialogue问"
            "‘他以前真有那么厉害？’，device=listener_qa、serves=[前史event]；下一镜楚媚用"
            "visible_dialogue回答‘十岁九段。’，device=listener_qa、serves同一event；再由姐妹追问"
            "‘后来呢？’，楚媚回答当前章已有的陨落事实。问与答分shot，不能改回旁白或内心声。"
            + (
                "native_dialogue下禁止narration和inner_voice；时间跳转只能用title_card，"
                "其他信息改成可见/画外对白或动作。"
                if native_dialogue
                else ""
            )
            + "严格输出JSON。"
        )
        user = (
            f"当前章节：{episode.source_title}\n"
            f"当前RetentionBeat：{beat.model_dump_json()}\n"
            f"本beat允许事件：{json.dumps([event.model_dump(mode='json') for event in events], ensure_ascii=False)}\n"
            f"incoming_close_state：{previous_close_state}\n"
            f"此前已释放fact_id：{json.dumps(released_fact_ids, ensure_ascii=False)}\n"
            f"本集契约：{episode_contract.model_dump_json() if episode_contract else '{}'}\n"
            f"角色与地点：{bible.model_dump_json()}\n"
            f"当前章原文：{episode.source_text}\n"
            f"JSON Schema：{json.dumps(RetentionBeatScript.model_json_schema(), ensure_ascii=False)}"
        )
        attempt = max(
            (
                int(match.group(1))
                for path in beat_root.glob("script_attempt_*.json")
                if (match := re.search(r"script_attempt_(\d+)\.json$", path.name))
            ),
            default=0,
        )

        def request(repair: dict | None) -> dict:
            nonlocal attempt
            attempt += 1
            if repair is not None:
                repair = {**repair, "resample": True}
            data = self._json(
                system,
                user,
                repair,
                token_budget=BEAT_SCRIPT_TOKEN_BUDGET,
            )
            atomic_write_json(
                beat_root / f"script_attempt_{attempt:02d}.json",
                data,
            )
            return data

        return _bounded_validate(
            f"plan_retention_beat_script:{beat.beat_id}",
            self.settings.planner_beat_max_retries,
            request,
            lambda data: _validate_retention_beat_script(
                data,
                beat=beat,
                episode=episode,
                bible=bible,
                native_dialogue=native_dialogue,
                episode_contract=episode_contract,
                released_fact_ids=released_fact_ids,
                diagnosis=diagnosis,
            ),
        )

    def _plan_retention_beat_direction(
        self,
        *,
        beat: RetentionBeat,
        script: RetentionBeatScript,
        bible: StoryBible,
        beat_root: Path,
    ) -> RetentionBeatDirection:
        native_dialogue = self.settings.final_audio_policy == NATIVE_DIALOGUE_POLICY
        system = (
            "你只负责已接受RetentionBeat剧本的导演化。不得改写、增删、重排任何turn文本、"
            "speaker、event_id、source_quote、change或shot_intent。输出direction-only映射："
            "source_shot_index与1-based turn_start/turn_end必须把每个源shot的turn连续且恰好覆盖一次。"
            "若一个镜头的动作或计划时长超过14秒，允许且必须在turn边界拆成多个DirectedShot。"
            "每个MotionBeat必须填写seconds、actor、target、action_type、end_state；"
            "每个DirectedShot必须填写script_open_state/script_close_state的knowledge、power、"
            "relationship、physical、ongoing_action五维；相邻镜逐字段完全衔接。"
            "performance只写大动作，physics只能补充不能覆盖故事动作。"
            "禁止指甲刺掌、血珠、流血、伤口、破皮，以及眉头微锁、眼睫微颤、喉结滚动、细微颤动、"
            "嘴角勾起、眼神空洞这类视频模型不可稳定表达的文学微表演；用站位、转身、走动、"
            "抬手、放手、递物、挡路、绕开等可见大动作。"
            "默认使用慢推、短跟拍或有动机的克制重构；一个镜头只保留一条运镜轨迹。"
            + (
                "audio_plan统一speech_strategy=native，不准备TTS或reference_audio。"
                if native_dialogue
                else "audio_plan统一speech_strategy=locked。"
            )
            + "严格输出JSON。"
        )
        user = (
            f"RetentionBeat：{beat.model_dump_json()}\n"
            f"已接受且不可修改的剧本：{script.model_dump_json()}\n"
            f"资产圣经：{bible.model_dump_json()}\n"
            f"JSON Schema：{json.dumps(RetentionBeatDirection.model_json_schema(), ensure_ascii=False)}"
        )
        attempt = max(
            (
                int(match.group(1))
                for path in beat_root.glob("direction_attempt_*.json")
                if (match := re.search(r"direction_attempt_(\d+)\.json$", path.name))
            ),
            default=0,
        )

        def request(repair: dict | None) -> dict:
            nonlocal attempt
            attempt += 1
            if repair is not None:
                repair = {**repair, "resample": True}
            data = self._json(
                system,
                user,
                repair,
                token_budget=BEAT_DIRECTION_TOKEN_BUDGET,
            )
            atomic_write_json(
                beat_root / f"direction_attempt_{attempt:02d}.json",
                data,
            )
            return data

        return _bounded_validate(
            f"plan_retention_beat_direction:{beat.beat_id}",
            self.settings.planner_beat_max_retries,
            request,
            lambda data: _validate_retention_beat_direction(
                data,
                beat=beat,
                script=script,
                native_dialogue=native_dialogue,
            ),
        )

    def _plan_episode_by_retention_beats(
        self,
        *,
        novel: NovelDocument,
        episode: Episode,
        bible: StoryBible,
        diagnosis: ChapterDiagnosis,
        showrunner: ShowrunnerPlan,
        previous_state: SeriesState | None,
        draft_root: Path,
        episode_contract: EpisodeContract | None = None,
    ) -> EpisodePlanningBundle:
        scripts = []
        directions = []
        previous_close = diagnosis.chapter_start_state
        released_fact_ids: list[str] = []
        for beat in showrunner.retention.beats:
            _check_planning_deadline(self, f"{beat.beat_id}:start")
            beat_root = draft_root / "beats" / beat.beat_id
            accepted_script_path = beat_root / "script_accepted.json"
            script = None
            if accepted_script_path.is_file():
                try:
                    script = _validate_retention_beat_script(
                        json.loads(
                            accepted_script_path.read_text(encoding="utf-8")
                        ),
                        beat=beat,
                        episode=episode,
                        bible=bible,
                        native_dialogue=(
                            self.settings.final_audio_policy
                            == NATIVE_DIALOGUE_POLICY
                        ),
                        episode_contract=episode_contract,
                        released_fact_ids=released_fact_ids,
                        diagnosis=diagnosis,
                    )
                except (ValidationError, ValueError):
                    stale = beat_root / "script_accepted.stale-v5-policy.json"
                    if not stale.is_file():
                        shutil.copy2(accepted_script_path, stale)
            if script is None:
                for attempt_path in sorted(
                    beat_root.glob("script_attempt_*.json"),
                    reverse=True,
                ):
                    try:
                        script = _validate_retention_beat_script(
                            json.loads(attempt_path.read_text(encoding="utf-8")),
                            beat=beat,
                            episode=episode,
                            bible=bible,
                            native_dialogue=(
                                self.settings.final_audio_policy
                                == NATIVE_DIALOGUE_POLICY
                            ),
                            episode_contract=episode_contract,
                            released_fact_ids=released_fact_ids,
                            diagnosis=diagnosis,
                        )
                        break
                    except (ValidationError, ValueError):
                        continue
                if script is None:
                    try:
                        script = self._plan_retention_beat_script(
                            episode=episode,
                            diagnosis=diagnosis,
                            bible=bible,
                            beat=beat,
                            previous_close_state=previous_close,
                            released_fact_ids=released_fact_ids,
                            beat_root=beat_root,
                            episode_contract=episode_contract,
                        )
                    except EpisodePlanningFailed:
                        raise
                    except Exception as error:
                        raise EpisodePlanningFailed(
                            f"{beat.beat_id} script retry budget exhausted: {error}",
                            episode_index=episode.index,
                            failed_stage="retention_beat_script",
                            failed_beat_id=beat.beat_id,
                            attempts=self.settings.planner_beat_max_retries + 1,
                            elapsed_seconds=time.monotonic() - self._planning_started,
                            intermediate_root=draft_root,
                        ) from error
                atomic_write_json(
                    accepted_script_path,
                    script.model_dump(mode="json"),
                )
            accepted_direction_path = beat_root / "direction_accepted.json"
            direction_source_attempt: Path | None = None
            direction = None
            if accepted_direction_path.is_file():
                try:
                    direction = _validate_retention_beat_direction(
                        json.loads(
                            accepted_direction_path.read_text(encoding="utf-8")
                        ),
                        beat=beat,
                        script=script,
                        native_dialogue=(
                            self.settings.final_audio_policy
                            == NATIVE_DIALOGUE_POLICY
                        ),
                    )
                except (ValidationError, ValueError):
                    stale = beat_root / "direction_accepted.stale-v5-policy.json"
                    if not stale.is_file():
                        shutil.copy2(accepted_direction_path, stale)
            if direction is None:
                for attempt_path in sorted(
                    beat_root.glob("direction_attempt_*.json"),
                    reverse=True,
                ):
                    try:
                        direction = _validate_retention_beat_direction(
                            json.loads(attempt_path.read_text(encoding="utf-8")),
                            beat=beat,
                            script=script,
                            native_dialogue=(
                                self.settings.final_audio_policy
                                == NATIVE_DIALOGUE_POLICY
                            ),
                        )
                        direction_source_attempt = attempt_path
                        break
                    except (ValidationError, ValueError):
                        continue
                if direction is None:
                    try:
                        direction = self._plan_retention_beat_direction(
                            beat=beat,
                            script=script,
                            bible=bible,
                            beat_root=beat_root,
                        )
                    except EpisodePlanningFailed:
                        raise
                    except Exception as error:
                        raise EpisodePlanningFailed(
                            f"{beat.beat_id} direction retry budget exhausted: {error}",
                            episode_index=episode.index,
                            failed_stage="retention_beat_direction",
                            failed_beat_id=beat.beat_id,
                            attempts=self.settings.planner_beat_max_retries + 1,
                            elapsed_seconds=time.monotonic() - self._planning_started,
                            intermediate_root=draft_root,
                        ) from error
                    for attempt_path in sorted(
                        beat_root.glob("direction_attempt_*.json"),
                        reverse=True,
                    ):
                        try:
                            candidate = _validate_retention_beat_direction(
                                json.loads(
                                    attempt_path.read_text(encoding="utf-8")
                                ),
                                beat=beat,
                                script=script,
                                native_dialogue=(
                                    self.settings.final_audio_policy
                                    == NATIVE_DIALOGUE_POLICY
                                ),
                            )
                        except (ValidationError, ValueError):
                            continue
                        if candidate == direction:
                            direction_source_attempt = attempt_path
                            break
                latest_direction_attempt = direction_source_attempt
                if latest_direction_attempt is not None:
                    raw_payload = json.loads(
                        latest_direction_attempt.read_text(encoding="utf-8")
                    )
                    try:
                        raw_direction = RetentionBeatDirection.model_validate(
                            raw_payload
                        )
                        normalized_changed = raw_direction != direction
                    except ValidationError:
                        normalized_changed = True
                    if normalized_changed:
                        atomic_write_json(
                            beat_root / "direction_normalization.json",
                            {
                                "policy": "provider-capability-and-handoff-normalization-v1",
                                "source_attempt": latest_direction_attempt.name,
                                "changes": [
                                    "convert global turn ranges to source-shot local ranges when unambiguous",
                                    "convert source-shot-index placeholders to full local ranges for single-speaker shots",
                                    "extend a contiguous final range over compiler-split tail turns with the same speaker and delivery",
                                    "collapse duplicate silent-action coverage to the final consequence direction",
                                    "inherit each shot open handoff from the previous close handoff",
                                    "translate nail/injury and literary micro-performance wording into visible large-action tension",
                                    "drop non-executable native audio beats",
                                    "bind moving-camera default motivation to the source shot change",
                                ],
                                "writing_fields_changed": False,
                            },
                        )
                atomic_write_json(
                    accepted_direction_path,
                    direction.model_dump(mode="json"),
                )
            scripts.append(script)
            directions.append(direction)
            previous_close = direction.shots[-1].performance_plan.end_state
            released_fact_ids.extend(
                fact_id
                for fact_id in script.released_fact_ids
                if fact_id not in released_fact_ids
            )
        plan = _compile_retention_beat_episode(
            episode=episode,
            diagnosis=diagnosis,
            showrunner=showrunner,
            scripts=scripts,
            directions=directions,
            creative_profile=self.settings.creative_profile,
            native_dialogue=(
                self.settings.final_audio_policy == NATIVE_DIALOGUE_POLICY
            ),
            episode_contract=episode_contract,
        )
        plan = normalize_chronological_plan(plan, diagnosis, episode)
        atomic_write_json(
            draft_root / "episode_script_assembled.json",
            plan.model_dump(mode="json"),
        )
        deterministic = evaluate_script_quality(
            plan,
            diagnosis,
            episode,
            previous_state=previous_state,
        )
        atomic_write_json(
            draft_root / "episode_structural_review.json",
            deterministic.model_dump(mode="json"),
        )
        if not deterministic.passed:
            failed_shot = next(
                (
                    issue.shot_indexes[0]
                    for issue in deterministic.issues
                    if issue.severity == "blocking" and issue.shot_indexes
                ),
                None,
            )
            failed_beat = next(
                (
                    beat.beat_id
                    for beat in plan.showrunner_plan.retention.beats
                    if failed_shot in beat.shot_indexes
                ),
                None,
            ) if plan.showrunner_plan is not None else None
            raise EpisodePlanningFailed(
                "assembled retention-beat script failed structural/reviewed gates",
                episode_index=episode.index,
                failed_stage="episode_structural_review",
                failed_beat_id=failed_beat,
                attempts=1,
                elapsed_seconds=time.monotonic() - self._planning_started,
                intermediate_root=draft_root,
            )
        qualitative = self._review_episode(
            episode,
            diagnosis,
            plan,
            previous_state,
        )
        pruning_rounds = []
        for review_round in range(1, 3):
            pruned_plan, removed_shots = _prune_redundant_derived_shots(
                plan,
                qualitative,
            )
            if not removed_shots:
                break
            plan = pruned_plan
            deterministic = evaluate_script_quality(
                plan,
                diagnosis,
                episode,
                previous_state=previous_state,
            )
            pruning_rounds.append(
                {
                    "round": review_round,
                    "removed_shot_indexes_before_rebind": removed_shots,
                    "remaining_shot_count": len(plan.shots),
                    "structural_passed": deterministic.passed,
                }
            )
            atomic_write_json(
                draft_root / "episode_review_pruning.json",
                {
                    "policy": "derived-deletion-test-v1",
                    "rounds": pruning_rounds,
                },
            )
            atomic_write_json(
                draft_root / "episode_script_assembled.json",
                plan.model_dump(mode="json"),
            )
            atomic_write_json(
                draft_root / "episode_structural_review.json",
                deterministic.model_dump(mode="json"),
            )
            if not deterministic.passed:
                raise EpisodePlanningFailed(
                    "review-guided derived-shot pruning broke structural gates",
                    episode_index=episode.index,
                    failed_stage="episode_review_pruning",
                    attempts=review_round,
                    elapsed_seconds=time.monotonic() - self._planning_started,
                    intermediate_root=draft_root,
                )
            qualitative = self._review_episode(
                episode,
                diagnosis,
                plan,
                previous_state,
            )
        qualitative = _downgrade_unremovable_review_deletion_claims(
            plan,
            qualitative,
        )
        report = evaluate_script_quality(
            plan,
            diagnosis,
            episode,
            qualitative=qualitative,
            previous_state=previous_state,
        )
        atomic_write_json(
            draft_root / "episode_independent_review.json",
            report.model_dump(mode="json"),
        )
        if not report.passed:
            raise EpisodePlanningFailed(
                "assembled retention-beat script failed independent review",
                episode_index=episode.index,
                failed_stage="episode_independent_review",
                attempts=1,
                elapsed_seconds=time.monotonic() - self._planning_started,
                intermediate_root=draft_root,
            )
        try:
            state = self._update_series_state(
                episode,
                bible,
                diagnosis,
                plan,
                previous_state,
            )
        except (ValidationError, ValueError) as state_error:
            raise EpisodePlanningFailed(
                f"series state update failed: {state_error}",
                episode_index=episode.index,
                failed_stage="series_state_update",
                attempts=self.settings.planner_max_revisions + 1,
                elapsed_seconds=time.monotonic() - self._planning_started,
                intermediate_root=draft_root,
            ) from state_error
        return EpisodePlanningBundle(
            diagnosis=diagnosis,
            plan=plan,
            quality_report=report,
            updated_series_state=state,
            episode_contract=episode_contract,
        )

    def plan_episode_bundle(
        self,
        novel: NovelDocument,
        episode: Episode,
        bible: StoryBible,
        previous_state: SeriesState | None = None,
    ) -> EpisodePlanningBundle:
        draft_root = (
            self.settings.output_root.resolve()
            / novel.novel_id
            / "script_drafts"
            / f"episode_{episode.index:03d}"
        )
        _begin_planning_deadline(self, self.settings, episode, draft_root)
        if self.settings.creative_profile == SHORT_DRAMA_PROFILE:
            series_development, series_diagnoses = self._ensure_series_development(
                novel,
                bible,
            )
        else:
            series_development, series_diagnoses = None, None
        diagnosis = (
            series_diagnoses[episode.index - 1]
            if series_diagnoses is not None
            else self._diagnose_episode(episode, bible, previous_state)
        )
        atomic_write_json(
            draft_root / "chapter_diagnosis.json",
            diagnosis.model_dump(mode="json"),
        )
        showrunner = None
        if self.settings.creative_profile == SHORT_DRAMA_PROFILE:
            accepted_showrunner = draft_root / "showrunner_plan.json"
            if accepted_showrunner.is_file():
                projection = series_development.chapter_projections[
                    episode.index - 1
                ]
                showrunner = self._validate_showrunner_data(
                    json.loads(accepted_showrunner.read_text(encoding="utf-8")),
                    episode,
                    diagnosis,
                    bible,
                    set(projection.allowed_reveal_event_ids),
                    True,
                )
            else:
                showrunner = self._plan_showrunner(
                    episode,
                    diagnosis,
                    bible,
                    previous_state,
                    series_development,
                )
        if showrunner is not None:
            episode_contract = plan_episode_contract(
                development=series_development,
                diagnosis=diagnosis,
                showrunner=showrunner,
                episode_index=episode.index,
            )
            atomic_write_json(
                draft_root / "showrunner_plan.json",
                showrunner.model_dump(mode="json"),
            )
            atomic_write_json(
                draft_root / "episode_contract.json",
                episode_contract.model_dump(mode="json"),
            )
            return self._plan_episode_by_retention_beats(
                novel=novel,
                episode=episode,
                bible=bible,
                diagnosis=diagnosis,
                showrunner=showrunner,
                previous_state=previous_state,
                draft_root=draft_root,
                episode_contract=episode_contract,
            )
        schema = EpisodePlan.model_json_schema()
        source_chars = len(re.sub(r"\s+", "", episode.source_text))
        policy = effective_script_policy(
            source_chars,
            diagnosis.density,
            self.settings.creative_profile,
        )
        size_guidance = (
            f"A/B密度参考为约{policy.min_script_chars}个有效发声字、{policy.min_turns}个turn、"
            f"{policy.min_shots}个shot；这些不是硬门，不得拆turn凑数，只在有新信息、动作或反应时增加"
        )
        direction_brief = creative_prompt_brief(bible.genre)
        native_dialogue = self.settings.final_audio_policy == NATIVE_DIALOGUE_POLICY
        voice_delivery_rule = (
            "native_dialogue配置下禁止narration和inner_voice；信息必须外化为visible_dialogue、"
            "offscreen_dialogue或可见动作，时间跳转使用delivery_mode=title_card的后期时间卡。"
            if native_dialogue
            else "原文中的内心声和画外对白可用角色音色，但speaking必须为false并设置"
            "inner_voice或offscreen_dialogue。"
        )
        system = (
            "你是连续竖屏短剧的逐章编剧和改编导演，不是有声书摘要员。当前章完整对应当前一集，不得拆集、"
            "合并下一章或借用后文事件；保留原文事实、人物关系、关键因果和章末边界，但可重排当前章信息。"
            f"creative_profile必须填写{self.settings.creative_profile}。{direction_brief}"
            "先填写dramaturgy，只选择一个dramatic_question和3-5个冲突节点；"
            "cold_open必须在0-3秒呈现当前章内最易读的受压结果、关系异常、关键道具或行动后果，"
            "cold_open_source_quote必须逐字取自当前章的某一整行，且这一行要短——""它必须能完整出现在前两镜的source_quote里（每镜source_quote上限120字），""所以不要选很长的段落做冷开场证据；前两镜必须实际呈现它。""如果预览后段事件，随后回到原因，"
            "并在正常因果位置再次完整兑现。不得提前给出后文章节答案。"
            "Showrunner已经独立完成留存、信息差和人物状态决策；必须按输入中的showrunner_plan安排事件和镜头，"
            "不得在剧本阶段重写其事实、时间节点或人物状态。showrunner_plan字段可原样复制，程序会依据event_ids"
            "确定性绑定最终shot_indexes。不得为了填留存节点虚构刺激。"
            "落笔前执行前置条件先行：每个揭示、反转、能力变化或人物主动选择，都先列出观众理解它所必需的"
            "事实、规则、关系与代价，并把这些前置beat落实到更小shot index，不能放在同镜末尾或事后解释。"
            "表现人物从强到弱、从高位跌落或关键机制失效时，先用当前章证据具体建立此前能力、地位及机制为何重要，"
            "再展示失去或破坏；只有结果没有before状态不算完整因果。"
            "人物做出逆人群、跨阶层、追随、背叛或公开站队等高代价行动前，先建立双方关系和行动动机，"
            "并在行动发生时安排旁人或对手的可见反应来表现代价。后续对白不能反向补足此前尚未成立的动机。"
            f"有效剧本目标为{size_guidance}。每个shot必须填写event_ids，"
            "每个章节事件必须写入adaptation_ledger，critical事件不得removed。"
            "承载因果、动机、来历或转折的叙述事件用externalized，把叙述改写成可见对白、反应或道具结果；"
            "supporting事件可compressed或merged，texture事件可removed；不要为覆盖原文把所有句子都发声。"
            "小说通常只有两成文字带引号，其余是叙述。把叙述一律压成旁白会让因果消失，"
            "所以每个turn必须声明derivation："
            "derivation=verbatim时turn.text必须逐字出现在source_quote的引号内容里；"
            "derivation=derived时source_quote必须是含叙述的原文，你据此把叙述外化为该场景中"
            "某个角色真的会说出口的话、或一次可拍摄的反应；允许无名听者、既有事实的获取桥接和反应镜，"
            "但不能发明information_state事实、原文事件或StoryBible之外的具名角色。每条derived填写serves。"
            "不得把原文引号内的台词改写成近似句：台词要么逐字引用，要么由叙述外化。"
            "带引号的原文台词必须归给具体角色，绝不能标成旁白。"
            "旁白只保留无法表演的时间、空间、必要规则和内心转折；能用动作、人物对白、反应、道具结果"
            "表达的信息必须改写成derived对白或反应，而不是压成旁白。"
            "旁白字数占比不得超过dramaturgy.narration_budget_ratio。"
            "使用口语化短剧节拍：每个turn只交付一个核心事实、动作或反应，但必须是一口气自然说完的完整语义句，"
            "目标不超过14字，硬上限20字；字幕在音频对齐后独立切页，严禁为字幕长度把一句话拆碎；"
            "需要讲因果时按触发→事实→后果→人物反应排列，不得只写模糊情绪。"
            "长段来历、回忆或规则说明不要交给单个角色一口气独白，"
            "拆成有听者的一问一答，让悬念由角色问出来。"
            "每个turn只允许一个声音角色；可见对白设置visible_dialogue；"
            f"{voice_delivery_rule}"
            "同一shot里的可见对白必须是同一个说话者；说话者、delivery_mode或主要视觉动作变化时新建shot。"
            "连续对话用建立镜、说话者近景、无声反应、反打和道具插入形成覆盖，"
            "同一角色保持屏幕侧但轮换胸像、紧肩部近景和较宽腰上景，不能复制同一构图。"
            "每镜performance_plan按触发→察觉→一个主要动作→对方反应→收束组织；"
            "不要为了防静态而让人物每1-2秒机械地转头、摆手或改变重心。"
            "抽象情绪必须翻译成不超过三个可见信号；涉及碎裂、撞击、奔跑、战气或强视效时，"
            "按准备→发力→接触→反作用→落定组织，并写少量同方向环境反馈，时长不足必须拆镜。"
            "camera_plan按镜头目的选择：空间建立用极慢推进，人物明确位移用短跟拍，信息揭示或权力变化用克制收紧；"
            "只有强调型大运镜受20%预算限制，普通慢推、短跟拍可相邻但每镜仍只允许一种清楚轨迹；"
            "显式shot动作和camera_plan冲突时先修正camera_plan以服务shot动作，不得把跟拍或推进强行写回固定机位。"
            "同场景锁定行动轴、人物左右和视线方向。"
            "每镜shot_intent必须解释dramatic_function、power_relation、emotion_target、viewer_focus，"
            "dramatic_function只能从establish/advance/pressure/withhold/reveal/payoff/reaction/transition/cliffhanger中选，"
            "不得使用reversal/climax/escalation/hook/question等留存节点名称；"
            "并绑定retention_beat_id；承担信息揭示的镜头引用information_fact_ids。先有语义意图，再选择景别和机位。"
            "每镜change必须用一句话写明本镜结束时观众新知道什么、关系如何变化或动作造成什么结果；"
            "change为空说明该镜没有戏剧功能，必须删除或与相邻镜合并。主角平均每三镜至少一次选择、反击、追问或改变路线的主动动作；"
            "至少20%的镜头要让具名对手与主角发生可见冲突，匿名人群嘲笑不能替代对手。"
            "derivation=verbatim只保留不可替代的原句，逐字turn占比不得超过35%，保真由事件、因果和source trace保证。"
            "最后一镜必须让问题、异象、决定或后果真实出现在动作或声音里，不能只写在next_preview或剧本注释中。"
            "visual_strategy必须明确：单人普通对白/反应用direct-assets，无人物空镜用scene-only；"
            "多人精确站位、人物道具交互、结果揭示、高潮反转和封面级构图用story-keyframe，"
            "并在keyframe_reasons记录原因。"
            "audio_plan中speech_strategy统一为native，让视频模型生成并保留原声，不得规划或准备TTS/reference_audio；"
            "本配置完全不使用TTS。"
            "ambience、music_cue和sfx_events写场景声音意图，不得把音效写成旁白。"
            "audio_beats使用0-1相对位置，cue_type只能取silence、ambience、impact、music_rise、music_cut、"
            "bass_drop、heartbeat、sfx、duck、release之一，不得自造也不要用中文；"
            "performance_plan.motion_beats.phase和camera_plan.camera_beats.phase只能取"
            "opening、development、resolution之一；"
            "每个声音变化必须写明台词、动作、揭示或反应触发，并绑定本镜留存节点，不得机械铺满整镜。"
            "不要重复Schema说明，不要在字段中写长篇方法论。"
            "对于长章节，每个连续shot通常承载1-3个语义turn，只表达一个明确视觉或情绪beat；"
            "优先讲清关键因果，不用文学性外貌铺陈或摘要旁白凑字数；"
            "字数只统计turns.text，narration、subtitle、visual_prompt不计入有效剧本字数。"
            "前10秒必须建立人物、异常和即时问题，章末反转必须先铺垫后兑现。严格输出JSON。"
        )
        base_user = (
            f"小说：{novel.title}\n当前章节：{episode.source_title}\n"
            f"章节诊断：{diagnosis.model_dump_json()}\n"
            f"独立Showrunner计划：{showrunner.model_dump_json() if showrunner else '{}'}\n"
            f"上一集状态：{previous_state.model_dump_json() if previous_state else '{}'}\n"
            f"故事圣经：{bible.model_dump_json()}\n当前章原文：{episode.source_text}\n"
            f"JSON Schema：{json.dumps(schema, ensure_ascii=False)}"
        )
        repair: dict | None = None
        last_report: ScriptQualityReport | None = None
        for revision in range(self.settings.planner_max_revisions + 1):
            draft_number = revision + 1
            data: dict | None = None
            try:
                data = self._json(
                    system,
                    base_user,
                    repair,
                    token_budget=SCRIPT_TOKEN_BUDGET,
                )
                atomic_write_json(draft_root / f"draft_{draft_number:02d}.json", data)
                plan = self._validate_episode_data(data, episode, bible)
                if showrunner is not None:
                    plan = plan.model_copy(update={"showrunner_plan": showrunner})
                plan = apply_creative_direction(
                    plan,
                    diagnosis,
                    bible,
                    profile=self.settings.creative_profile,
                )
                plan = normalize_chronological_plan(plan, diagnosis, episode)
                atomic_write_json(
                    draft_root / f"draft_{draft_number:02d}_normalized.json",
                    plan.model_dump(mode="json"),
                )
                deterministic = evaluate_script_quality(
                    plan, diagnosis, episode, previous_state=previous_state
                )
                issue_codes = {issue.code for issue in deterministic.issues}
                if not deterministic.passed and issue_codes <= {
                    "script_too_short",
                    "too_few_turns",
                }:
                    required_chars = effective_script_policy(
                        len(re.sub(r"\s+", "", episode.source_text)),
                        diagnosis.density,
                        self.settings.creative_profile,
                    ).min_script_chars
                    plan = self._expand_script_turns(
                        episode,
                        bible,
                        diagnosis,
                        plan,
                        required_chars,
                        previous_state,
                    )
                    atomic_write_json(
                        draft_root / f"draft_{draft_number:02d}_expanded.json",
                        plan.model_dump(mode="json"),
                    )
                    deterministic = evaluate_script_quality(
                        plan, diagnosis, episode, previous_state=previous_state
                    )
                    atomic_write_json(
                        draft_root / f"draft_{draft_number:02d}_expanded_validation.json",
                        deterministic.model_dump(mode="json"),
                    )
                blocking_codes = {
                    issue.code
                    for issue in deterministic.issues
                    if issue.severity == "blocking"
                }
                if (
                    not deterministic.passed
                    and blocking_codes & DIALOGUE_ATTRIBUTION_CODES
                    and blocking_codes <= ATTRIBUTION_REPAIR_CODES
                ):
                    plan = self._repair_turn_attribution(
                        episode,
                        bible,
                        diagnosis,
                        plan,
                        deterministic,
                        previous_state,
                    )
                    atomic_write_json(
                        draft_root / f"draft_{draft_number:02d}_attribution.json",
                        plan.model_dump(mode="json"),
                    )
                    deterministic = evaluate_script_quality(
                        plan, diagnosis, episode, previous_state=previous_state
                    )
                    atomic_write_json(
                        draft_root
                        / f"draft_{draft_number:02d}_attribution_validation.json",
                        deterministic.model_dump(mode="json"),
                    )
                if not deterministic.passed:
                    raise ValueError(deterministic.model_dump_json())
                report = self._review_episode(episode, diagnosis, plan, previous_state)
                last_report = report
                atomic_write_json(
                    draft_root / f"draft_{draft_number:02d}_validation.json",
                    report.model_dump(mode="json"),
                )
                blocking_review = [
                    issue for issue in report.issues if issue.severity == "blocking"
                ]
                if (
                    not report.passed
                    and blocking_review
                    and all(
                        any(token in issue.code.upper() for token in REVIEW_CONTENT_PATCH_TOKENS)
                        and issue.shot_indexes
                        for issue in blocking_review
                    )
                ):
                    plan = self._repair_review_content(
                        episode,
                        bible,
                        diagnosis,
                        plan,
                        report,
                        previous_state,
                    )
                    atomic_write_json(
                        draft_root / f"draft_{draft_number:02d}_content_patch.json",
                        plan.model_dump(mode="json"),
                    )
                    deterministic = evaluate_script_quality(
                        plan, diagnosis, episode, previous_state=previous_state
                    )
                    atomic_write_json(
                        draft_root
                        / f"draft_{draft_number:02d}_content_patch_validation.json",
                        deterministic.model_dump(mode="json"),
                    )
                    report = self._review_episode(
                        episode, diagnosis, plan, previous_state
                    )
                    last_report = report
                    atomic_write_json(
                        draft_root
                        / f"draft_{draft_number:02d}_content_patch_review.json",
                        report.model_dump(mode="json"),
                    )
                if report.passed:
                    state = self._update_series_state(
                        episode, bible, diagnosis, plan, previous_state
                    )
                    return EpisodePlanningBundle(
                        diagnosis=diagnosis,
                        plan=plan,
                        quality_report=report,
                        updated_series_state=state,
                    )
                raise ValueError(report.model_dump_json())
            except (ValidationError, ValueError) as error:
                validation_path = draft_root / f"draft_{draft_number:02d}_validation.json"
                if not validation_path.exists():
                    atomic_write_json(
                        validation_path,
                        {
                            "passed": False,
                            "stage": "deterministic_validation",
                            "errors": _validation_feedback(error),
                        },
                    )
                if revision >= self.settings.planner_max_revisions:
                    break
                repair = _validation_retry(
                    revision,
                    data,
                    error,
                    repair,
                )
        detail = last_report.model_dump_json() if last_report else json.dumps(
            repair or {}, ensure_ascii=False
        )
        raise EpisodePlanningFailed(
            f"script quality gate remained invalid: {detail}",
            episode_index=episode.index,
            failed_stage="episode_script_review",
            attempts=self.settings.planner_max_revisions + 1,
            elapsed_seconds=time.monotonic() - self._planning_started,
            intermediate_root=draft_root,
        )


class CommandPlanner(Planner):
    """Model-neutral planner adapter using a small JSON file contract.

    The configured command receives ``--operation``, ``--input`` and ``--output``.
    It can call any local model, hosted model, or orchestration service and must write
    JSON matching the requested schema.
    """

    def __init__(self, settings: Settings):
        if not settings.planner_command:
            raise ValueError("planner command is missing")
        self.command = shlex.split(settings.planner_command)
        self.settings = settings
        self.max_revisions = settings.planner_max_revisions
        self.creative_profile = settings.creative_profile

    def _ensure_series_development(
        self,
        novel: NovelDocument,
        bible: StoryBible,
    ) -> tuple[SeriesDevelopmentPlan, list[ChapterDiagnosis]]:
        cached = getattr(self, "_series_development_cache", None)
        if cached is not None:
            return cached
        root = (
            self.settings.output_root.resolve()
            / novel.novel_id
            / "series_development"
        )
        active_path = root / "active.json"
        source_identity = hashlib.sha256(
            (
                novel.text
                + bible.style_fingerprint
                + "series-development-v2"
            ).encode("utf-8")
        ).hexdigest()
        if active_path.is_file():
            active = json.loads(active_path.read_text(encoding="utf-8"))
            version = str(active.get("development_version", ""))
            development_path = root / f"series_development.{version}.json"
            review_path = root / f"series_development_review.{version}.json"
            diagnoses_path = root / f"chapter_diagnoses.{version}.json"
            if (
                active.get("source_identity") == source_identity
                and development_path.is_file()
                and review_path.is_file()
                and diagnoses_path.is_file()
            ):
                diagnoses = [
                    ChapterDiagnosis.model_validate(row)
                    for row in json.loads(
                        diagnoses_path.read_text(encoding="utf-8")
                    )
                ]
                development = _validate_series_development(
                    json.loads(development_path.read_text(encoding="utf-8")),
                    novel=novel,
                    bible=bible,
                    diagnoses=diagnoses,
                    development_version=version,
                )
                review = SeriesDevelopmentReview.model_validate_json(
                    review_path.read_text(encoding="utf-8")
                )
                if review.passed and not review.future_fact_leakage:
                    self._series_development_cache = (development, diagnoses)
                    return development, diagnoses
        versions = [
            int(match.group(1))
            for path in root.glob("series_development.v*.json")
            if (match := re.search(r"\.v(\d{3})\.json$", path.name))
        ] if root.exists() else []
        version = f"v{max(versions, default=0) + 1:03d}"
        diagnoses = [
            _bounded_validate(
                f"diagnose_episode:{episode.index}",
                self.max_revisions,
                lambda repair, current=episode: self._invoke(
                    "diagnose_episode",
                    {
                        "contract": "novel-manga-planner/v5",
                        "episode": current.model_dump(mode="json"),
                        "story_bible": bible.model_dump(mode="json"),
                        "schema": ChapterDiagnosis.model_json_schema(),
                        **({"repair": repair} if repair else {}),
                    },
                ),
                lambda data, current=episode: validate_chapter_diagnosis(
                    ChapterDiagnosis.model_validate(data),
                    current,
                    bible,
                ),
            )
            for episode in novel.episodes
        ]
        development_attempt = max(
            (
                int(match.group(1))
                for path in root.glob(
                    f"series_development.{version}.attempt_*.raw.json"
                )
                if (
                    match := re.search(
                        r"\.attempt_(\d+)\.raw\.json$", path.name
                    )
                )
            ),
            default=0,
        )

        def request_development(repair: dict | None) -> dict:
            nonlocal development_attempt
            development_attempt += 1
            data = self._invoke(
                "develop_series",
                {
                    "contract": "novel-manga-planner/v5",
                    "novel_id": novel.novel_id,
                    "novel_title": novel.title,
                    "development_version": version,
                    "story_bible": bible.model_dump(mode="json"),
                    "chapter_diagnoses": [
                        row.model_dump(mode="json") for row in diagnoses
                    ],
                    "schema": SeriesDevelopmentPlan.model_json_schema(),
                    **({"repair": repair} if repair else {}),
                },
            )
            atomic_write_json(
                root
                / (
                    f"series_development.{version}.attempt_"
                    f"{development_attempt:02d}.raw.json"
                ),
                data,
            )
            return data

        development = _bounded_validate(
            "develop_series",
            self.max_revisions,
            request_development,
            lambda data: _validate_series_development(
                data,
                novel=novel,
                bible=bible,
                diagnoses=diagnoses,
                development_version=version,
            ),
        )
        atomic_write_json(
            root / f"series_development.{version}.candidate.json",
            development.model_dump(mode="json"),
        )
        review_attempt = max(
            (
                int(match.group(1))
                for path in root.glob(
                    f"series_development_review.{version}.attempt_*.raw.json"
                )
                if (
                    match := re.search(
                        r"\.attempt_(\d+)\.raw\.json$", path.name
                    )
                )
            ),
            default=0,
        )

        def request_review(repair: dict | None) -> dict:
            nonlocal review_attempt
            review_attempt += 1
            data = self._invoke(
                "review_series_development",
                {
                    "contract": "novel-manga-planner/v5",
                    "series_development": development.model_dump(mode="json"),
                    "chapter_diagnoses": [
                        row.model_dump(mode="json") for row in diagnoses
                    ],
                    "schema": SeriesDevelopmentReview.model_json_schema(),
                    **({"repair": repair} if repair else {}),
                },
            )
            atomic_write_json(
                root
                / (
                    f"series_development_review.{version}.attempt_"
                    f"{review_attempt:02d}.raw.json"
                ),
                data,
            )
            return data

        review = _bounded_validate(
            "review_series_development",
            self.max_revisions,
            request_review,
            SeriesDevelopmentReview.model_validate,
        )
        atomic_write_json(
            root / f"series_development_review.{version}.candidate.json",
            review.model_dump(mode="json"),
        )
        if (
            not review.passed
            or not review.engine_coherent
            or not review.projections_grounded
            or review.future_fact_leakage
        ):
            raise EpisodePlanningFailed(
                "series development review failed",
                episode_index=1,
                failed_stage="series_development_review",
                attempts=self.max_revisions + 1,
                elapsed_seconds=0.0,
                intermediate_root=root,
            )
        atomic_write_json(
            root / f"chapter_diagnoses.{version}.json",
            [row.model_dump(mode="json") for row in diagnoses],
        )
        atomic_write_json(
            root / f"series_development.{version}.json",
            development.model_dump(mode="json"),
        )
        atomic_write_json(
            root / f"series_development_review.{version}.json",
            review.model_dump(mode="json"),
        )
        atomic_write_json(
            active_path,
            {
                "schema_version": 1,
                "development_version": version,
                "source_identity": source_identity,
                "review_passed": True,
            },
        )
        self._series_development_cache = (development, diagnoses)
        return development, diagnoses

    def _invoke(self, operation: str, payload: dict) -> dict:
        _check_planning_deadline(self, operation)
        with tempfile.TemporaryDirectory(prefix="novel-planner-") as directory:
            root = Path(directory)
            request = root / "request.json"
            response = root / "response.json"
            request.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                subprocess.run(
                    self.command
                    + ["--operation", operation, "--input", str(request), "--output", str(response)],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=max(
                        0.1,
                        float(self._planning_deadline) - time.monotonic(),
                    )
                    if hasattr(self, "_planning_deadline")
                    else None,
                )
            except subprocess.TimeoutExpired as error:
                _check_planning_deadline(self, operation)
                raise RuntimeError(
                    f"planner command timed out for {operation}"
                ) from error
            except subprocess.CalledProcessError as error:
                detail = (error.stderr or error.stdout or "").strip()[-2000:]
                raise RuntimeError(
                    f"planner command failed for {operation}: {detail}"
                ) from error
            if not response.is_file():
                raise RuntimeError("planner command did not create its JSON output")
            _check_planning_deadline(self, operation)
            return json.loads(response.read_text(encoding="utf-8"))

    def build_bible(self, novel: NovelDocument) -> StoryBible:
        payload = {
            "contract": "novel-manga-planner/v2",
            "novel": {
                "novel_id": novel.novel_id,
                "title": novel.title,
                "text": _compact_excerpt(novel.text),
            },
            "schema": StoryBible.model_json_schema(),
            "requirements": {"style": STYLE, "source_faithful": True},
        }
        return _bounded_validate(
            "build_bible",
            self.max_revisions,
            lambda repair: self._invoke(
                "build_bible",
                {**payload, **({"repair": repair} if repair else {})},
            ),
            lambda data: _validate_story_bible(data, novel),
        )

    def plan_episode(self, novel: NovelDocument, episode: Episode, bible: StoryBible) -> EpisodePlan:
        payload = {
            "contract": "novel-manga-planner/v2",
            "novel_id": novel.novel_id,
            "episode": episode.model_dump(mode="json"),
            "story_bible": bible.model_dump(mode="json"),
            "schema": EpisodePlan.model_json_schema(),
            "requirements": {
                "one_visible_speaker_per_turn": True,
                "exact_turn_text": True,
                "source_quotes_required": True,
                "first_story_beat_within_seconds": 10,
                "performance_plan_required": True,
                "camera_plan_required": True,
                "camera_mode_default": "locked",
                "camera_move_requires_motivation": True,
                "one_camera_trajectory_per_shot": True,
                "dialogue_action_axis_locked": True,
                "reference_only_anchors_identity_costume_environment_style": True,
            },
        }
        normalizer = object.__new__(OpenAICompatiblePlanner)
        plan = _bounded_validate(
            "plan_episode",
            self.max_revisions,
            lambda repair: self._invoke(
                "plan_episode",
                {**payload, **({"repair": repair} if repair else {})},
            ),
            lambda data: normalizer._validate_episode_data(data, episode, bible),
        )
        return apply_creative_direction(
            plan,
            deterministic_chapter_diagnosis(episode),
            bible,
            profile=self.creative_profile,
        )

    def _plan_retention_beat_script(
        self,
        *,
        episode: Episode,
        diagnosis: ChapterDiagnosis,
        bible: StoryBible,
        beat: RetentionBeat,
        previous_close_state: str,
        released_fact_ids: list[str],
        beat_root: Path,
        episode_contract: EpisodeContract | None = None,
    ) -> RetentionBeatScript:
        attempt = max(
            (
                int(match.group(1))
                for path in beat_root.glob("script_attempt_*.json")
                if (match := re.search(r"script_attempt_(\d+)\.json$", path.name))
            ),
            default=0,
        )
        common = {
            "contract": "novel-manga-planner/v5",
            "episode": episode.model_dump(mode="json"),
            "chapter_diagnosis": diagnosis.model_dump(mode="json"),
            "retention_beat": beat.model_dump(mode="json"),
            "story_bible": bible.model_dump(mode="json"),
            "incoming_close_state": previous_close_state,
            "released_fact_ids": released_fact_ids,
            "episode_contract": (
                episode_contract.model_dump(mode="json")
                if episode_contract is not None
                else {}
            ),
            "schema": RetentionBeatScript.model_json_schema(),
            "requirements": {
                "writing_only": True,
                "shots_per_beat": [1, 6],
                "no_direction_fields": True,
                "anonymous_carriers_allowed_offscreen": True,
                "new_named_characters_forbidden": True,
                "new_facts_or_events_forbidden": True,
                "derived_serves_required": True,
                "derived_deletion_test": True,
                "silent_reactions_use_delivery_mode": "silent_action",
                "native_dialogue": (
                    self.settings.final_audio_policy == NATIVE_DIALOGUE_POLICY
                ),
                "native_forbidden_delivery_modes": [
                    "narration",
                    "inner_voice",
                ],
                "narrated_fact_carriers": [
                    "listener_qa",
                    "crowd_proxy",
                    "evidence_object",
                    "spatial",
                    "consequence",
                ],
                "past_facts_stay_in_current_timeline": True,
                "concrete_age_level_mechanism_facts_need_spoken_or_title_carrier": True,
                "flashback_forbidden": True,
            },
        }

        def request(repair: dict | None) -> dict:
            nonlocal attempt
            attempt += 1
            if repair is not None:
                repair = {**repair, "resample": True}
            data = self._invoke(
                "plan_beat_script",
                {**common, **({"repair": repair} if repair else {})},
            )
            atomic_write_json(
                beat_root / f"script_attempt_{attempt:02d}.json",
                data,
            )
            return data

        return _bounded_validate(
            f"plan_retention_beat_script:{beat.beat_id}",
            self.settings.planner_beat_max_retries,
            request,
            lambda data: _validate_retention_beat_script(
                data,
                beat=beat,
                episode=episode,
                bible=bible,
                native_dialogue=(
                    self.settings.final_audio_policy == NATIVE_DIALOGUE_POLICY
                ),
                episode_contract=episode_contract,
                released_fact_ids=released_fact_ids,
                diagnosis=diagnosis,
            ),
        )

    def _plan_retention_beat_direction(
        self,
        *,
        beat: RetentionBeat,
        script: RetentionBeatScript,
        bible: StoryBible,
        beat_root: Path,
    ) -> RetentionBeatDirection:
        attempt = max(
            (
                int(match.group(1))
                for path in beat_root.glob("direction_attempt_*.json")
                if (match := re.search(r"direction_attempt_(\d+)\.json$", path.name))
            ),
            default=0,
        )
        common = {
            "contract": "novel-manga-planner/v5",
            "retention_beat": beat.model_dump(mode="json"),
            "accepted_script": script.model_dump(mode="json"),
            "story_bible": bible.model_dump(mode="json"),
            "schema": RetentionBeatDirection.model_json_schema(),
            "requirements": {
                "direction_only": True,
                "turn_text_immutable": True,
                "split_only_at_turn_boundaries": True,
                "source_shot_index_set_must_match_script_exactly": True,
                "every_source_shot_requires_direction": True,
                "later_source_shot_cannot_be_absorbed_into_earlier_turns": True,
                "motion_beat_timing_required": True,
                "five_dimension_handoff_required": True,
                "literary_micro_performance_forbidden": True,
                "injury_detail_forbidden": True,
                "native_dialogue": (
                    self.settings.final_audio_policy == NATIVE_DIALOGUE_POLICY
                ),
            },
        }

        def request(repair: dict | None) -> dict:
            nonlocal attempt
            attempt += 1
            if repair is not None:
                repair = {**repair, "resample": True}
            data = self._invoke(
                "plan_beat_direction",
                {**common, **({"repair": repair} if repair else {})},
            )
            atomic_write_json(
                beat_root / f"direction_attempt_{attempt:02d}.json",
                data,
            )
            return data

        return _bounded_validate(
            f"plan_retention_beat_direction:{beat.beat_id}",
            self.settings.planner_beat_max_retries,
            request,
            lambda data: _validate_retention_beat_direction(
                data,
                beat=beat,
                script=script,
                native_dialogue=(
                    self.settings.final_audio_policy == NATIVE_DIALOGUE_POLICY
                ),
            ),
        )

    def _review_episode(
        self,
        episode: Episode,
        diagnosis: ChapterDiagnosis,
        plan: EpisodePlan,
        previous_state: SeriesState | None,
    ) -> ScriptQualityReport:
        review_root = getattr(self, "_planning_intermediate_root", None)
        review_input = {
            "shots": [
                {
                    "index": shot.index,
                    "event_ids": shot.event_ids,
                    "change": shot.change,
                    "visual_prompt": shot.visual_prompt,
                    "motion_prompt": shot.motion_prompt,
                    "turns": [
                        {
                            "text": turn.text,
                            "delivery_mode": turn.delivery_mode,
                            "derivation": turn.derivation,
                            "device": turn.device,
                            "serves": turn.serves,
                        }
                        for turn in shot.turns
                    ],
                }
                for shot in plan.shots
            ]
        }
        review_input_path = (
            review_root / "review_input.json"
            if isinstance(review_root, Path)
            else None
        )
        review_cache_matches = bool(
            review_input_path is not None
            and review_input_path.is_file()
            and json.loads(review_input_path.read_text(encoding="utf-8"))
            == review_input
        )
        accepted_path = (
            review_root / "review_accepted.json"
            if isinstance(review_root, Path)
            else None
        )
        candidates = (
            [
                accepted_path,
                *sorted(
                    review_root.glob("review_attempt_*.raw.json"),
                    reverse=True,
                ),
            ]
            if isinstance(review_root, Path) and review_cache_matches
            else []
        )
        for candidate in candidates:
            if candidate is None or not candidate.is_file():
                continue
            try:
                review = ScriptQualityReport.model_validate_json(
                    candidate.read_text(encoding="utf-8")
                )
                if accepted_path is not None:
                    atomic_write_json(
                        accepted_path,
                        review.model_dump(mode="json"),
                    )
                return review
            except (ValidationError, ValueError):
                continue
        attempt = max(
            (
                int(match.group(1))
                for path in review_root.glob("review_attempt_*.raw.json")
                if (
                    match := re.search(
                        r"review_attempt_(\d+)\.raw\.json$", path.name
                    )
                )
            ),
            default=0,
        ) if isinstance(review_root, Path) else 0

        def request_review(repair: dict | None) -> dict:
            nonlocal attempt
            attempt += 1
            data = self._invoke(
                "review_episode",
                {
                    "contract": "novel-manga-planner/v5",
                    "episode": episode.model_dump(mode="json"),
                    "chapter_diagnosis": diagnosis.model_dump(mode="json"),
                    "episode_plan": plan.model_dump(mode="json"),
                    "previous_state": (
                        previous_state.model_dump(mode="json")
                        if previous_state
                        else {}
                    ),
                    "schema": ScriptQualityReport.model_json_schema(),
                    "requirements": {
                        "derived_deletion_test": True,
                        "derived_serves_must_match_existing_event_or_fact": True,
                        "new_facts_events_named_characters_forbidden": True,
                    },
                    **({"repair": repair} if repair else {}),
                },
            )
            if isinstance(review_root, Path):
                atomic_write_json(
                    review_root / f"review_attempt_{attempt:02d}.raw.json",
                    data,
                )
            return data

        review = _bounded_validate(
            "review_episode",
            self.max_revisions,
            request_review,
            ScriptQualityReport.model_validate,
        )
        if accepted_path is not None:
            atomic_write_json(
                accepted_path,
                review.model_dump(mode="json"),
            )
        if review_input_path is not None:
            atomic_write_json(review_input_path, review_input)
        return review

    def _update_series_state(
        self,
        episode: Episode,
        bible: StoryBible,
        diagnosis: ChapterDiagnosis,
        plan: EpisodePlan,
        previous_state: SeriesState | None,
    ) -> SeriesState:
        state_root = getattr(self, "_planning_intermediate_root", None)
        accepted_path = (
            state_root / "series_state_accepted.json"
            if isinstance(state_root, Path)
            else None
        )

        def validate_state_data(value: dict) -> SeriesState:
            normalized, changes = _normalize_current_episode_state_quotes(
                value,
                episode,
            )
            if not normalized.get("previous_episode_end") and plan.shots:
                last_shot = plan.shots[-1]
                final_line = next(
                    (
                        turn.text
                        for turn in reversed(last_shot.turns)
                        if turn.delivery_mode
                        not in {
                            TurnDelivery.SILENT_ACTION,
                            TurnDelivery.TITLE_CARD,
                        }
                    ),
                    "",
                )
                normalized["previous_episode_end"] = {
                    "location": last_shot.location,
                    "action": diagnosis.chapter_end_state,
                    "final_line": final_line,
                    "final_visual": last_shot.change,
                    "evidence": {
                        "statement": diagnosis.chapter_end_state,
                        "source_episode": episode.index,
                        "source_quote": last_shot.source_quote,
                        "certainty": "confirmed",
                    },
                }
                changes.append("previous_episode_end")
            state = validate_series_state(
                SeriesState.model_validate(normalized),
                episode,
                previous_state,
            )
            setattr(self, "_series_state_normalization_changes", changes)
            return state

        candidates = (
            [
                accepted_path,
                *sorted(
                    state_root.glob("series_state_attempt_*.raw.json"),
                    reverse=True,
                ),
            ]
            if isinstance(state_root, Path)
            else []
        )
        for candidate in candidates:
            if candidate is None or not candidate.is_file():
                continue
            try:
                state = validate_state_data(
                    json.loads(candidate.read_text(encoding="utf-8"))
                )
                if accepted_path is not None:
                    atomic_write_json(
                        accepted_path,
                        state.model_dump(mode="json"),
                    )
                    changes = getattr(
                        self,
                        "_series_state_normalization_changes",
                        [],
                    )
                    if changes:
                        atomic_write_json(
                            state_root / "series_state_normalization.json",
                            {
                                "policy": "state-handoff-and-evidence-normalization-v1",
                                "changed_evidence_paths": changes,
                                "statement_fields_changed": False,
                            },
                        )
                return state
            except (ValidationError, ValueError):
                continue
        attempt = max(
            (
                int(match.group(1))
                for path in state_root.glob(
                    "series_state_attempt_*.raw.json"
                )
                if (
                    match := re.search(
                        r"series_state_attempt_(\d+)\.raw\.json$",
                        path.name,
                    )
                )
            ),
            default=0,
        ) if isinstance(state_root, Path) else 0

        def request_state(repair: dict | None) -> dict:
            nonlocal attempt
            attempt += 1
            data = self._invoke(
                "update_series_state",
                {
                    "contract": "novel-manga-planner/v5",
                    "episode": episode.model_dump(mode="json"),
                    "story_bible": bible.model_dump(mode="json"),
                    "chapter_diagnosis": diagnosis.model_dump(mode="json"),
                    "episode_plan": plan.model_dump(mode="json"),
                    "previous_state": (
                        previous_state.model_dump(mode="json")
                        if previous_state
                        else {}
                    ),
                    "schema": SeriesState.model_json_schema(),
                    **({"repair": repair} if repair else {}),
                },
            )
            if isinstance(state_root, Path):
                atomic_write_json(
                    state_root
                    / f"series_state_attempt_{attempt:02d}.raw.json",
                    data,
                )
            return data

        state = _bounded_validate(
            "update_series_state",
            self.max_revisions,
            request_state,
            validate_state_data,
        )
        if accepted_path is not None:
            atomic_write_json(
                accepted_path,
                state.model_dump(mode="json"),
            )
            changes = getattr(
                self,
                "_series_state_normalization_changes",
                [],
            )
            if changes:
                atomic_write_json(
                    state_root / "series_state_normalization.json",
                    {
                        "policy": "state-handoff-and-evidence-normalization-v1",
                        "changed_evidence_paths": changes,
                        "statement_fields_changed": False,
                    },
                )
        return state

    def plan_episode_bundle(
        self,
        novel: NovelDocument,
        episode: Episode,
        bible: StoryBible,
        previous_state: SeriesState | None = None,
    ) -> EpisodePlanningBundle:
        draft_root = (
            self.settings.output_root.resolve()
            / novel.novel_id
            / "script_drafts"
            / f"episode_{episode.index:03d}"
        )
        _begin_planning_deadline(self, self.settings, episode, draft_root)
        if self.creative_profile == SHORT_DRAMA_PROFILE:
            series_development, series_diagnoses = self._ensure_series_development(
                novel,
                bible,
            )
        else:
            series_development, series_diagnoses = None, None
        common = {
            "contract": "novel-manga-planner/v5",
            "novel_id": novel.novel_id,
            "episode": episode.model_dump(mode="json"),
            "story_bible": bible.model_dump(mode="json"),
            "previous_state": previous_state.model_dump(mode="json") if previous_state else {},
            "chapter_only": True,
            "future_chapters_allowed": False,
            "creative_profile": self.creative_profile,
        }
        diagnosis = (
            series_diagnoses[episode.index - 1]
            if series_diagnoses is not None
            else _bounded_validate(
                "diagnose_episode",
                self.max_revisions,
                lambda repair: self._invoke(
                    "diagnose_episode",
                    {
                        **common,
                        "schema": ChapterDiagnosis.model_json_schema(),
                        **({"repair": repair} if repair else {}),
                    },
                ),
                lambda data: validate_chapter_diagnosis(
                    ChapterDiagnosis.model_validate(data),
                    episode,
                    bible,
                ),
            )
        )
        normalizer = object.__new__(OpenAICompatiblePlanner)
        showrunner_attempt = max(
            (
                int(match.group(1))
                for path in draft_root.glob("showrunner_attempt_*.raw.json")
                if (
                    match := re.search(
                        r"showrunner_attempt_(\d+)\.raw\.json$", path.name
                    )
                )
            ),
            default=0,
        )

        def request_showrunner(repair: dict | None) -> dict:
            nonlocal showrunner_attempt
            showrunner_attempt += 1
            data = self._invoke(
                "plan_showrunner",
                {
                    **common,
                    "chapter_diagnosis": diagnosis.model_dump(mode="json"),
                    "series_engine_and_current_projection": (
                        _current_projection_context(
                            series_development,
                            episode.index,
                        )
                    ),
                    "schema": ShowrunnerPlan.model_json_schema(),
                    "requirements": {
                        "planning_mode": "planner",
                        "shot_indexes_must_be_empty": True,
                        "source_grounded_retention": True,
                        "information_states_required": True,
                        "character_state_deltas_for_diagnosed_changes": True,
                        "episode_mode_choice_cost_opposition_required": True,
                        "causes_before_dependents_or_dependent_replayed_after_cause": True,
                    },
                    **({"repair": repair} if repair else {}),
                },
            )
            atomic_write_json(
                draft_root
                / f"showrunner_attempt_{showrunner_attempt:02d}.raw.json",
                data,
            )
            return data

        def validate_showrunner(data: dict) -> ShowrunnerPlan:
            return normalizer._validate_showrunner_data(
                data,
                episode,
                diagnosis,
                bible,
                set(
                    series_development.chapter_projections[
                        episode.index - 1
                    ].allowed_reveal_event_ids
                ),
                True,
            )

        showrunner = None
        if self.creative_profile == SHORT_DRAMA_PROFILE:
            candidates = [
                draft_root / "showrunner.accepted.json",
                *sorted(
                    draft_root.glob("showrunner_attempt_*.raw.json"),
                    reverse=True,
                ),
            ]
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                try:
                    showrunner = validate_showrunner(
                        json.loads(candidate.read_text(encoding="utf-8"))
                    )
                    break
                except (ValidationError, ValueError):
                    continue
            if showrunner is None:
                showrunner = _bounded_validate(
                    "plan_showrunner",
                    self.max_revisions,
                    request_showrunner,
                    validate_showrunner,
                )
            atomic_write_json(
                draft_root / "showrunner.accepted.json",
                showrunner.model_dump(mode="json"),
            )
        if showrunner is not None:
            episode_contract = plan_episode_contract(
                development=series_development,
                diagnosis=diagnosis,
                showrunner=showrunner,
                episode_index=episode.index,
            )
            atomic_write_json(
                draft_root / "episode_contract.json",
                episode_contract.model_dump(mode="json"),
            )
            return OpenAICompatiblePlanner._plan_episode_by_retention_beats(
                self,
                novel=novel,
                episode=episode,
                bible=bible,
                diagnosis=diagnosis,
                showrunner=showrunner,
                previous_state=previous_state,
                draft_root=draft_root,
                episode_contract=episode_contract,
            )
        repair: dict | None = None
        last_report: ScriptQualityReport | None = None
        for revision in range(self.max_revisions + 1):
            data = self._invoke(
                "plan_episode",
                {
                    **common,
                    "chapter_diagnosis": diagnosis.model_dump(mode="json"),
                    "showrunner_plan": (
                        showrunner.model_dump(mode="json") if showrunner else {}
                    ),
                    "schema": EpisodePlan.model_json_schema(),
                    "requirements": {
                        "all_critical_events_mapped": True,
                        "adaptation_ledger_required": True,
                        "causal_chain_complete": True,
                        "creative_profile": self.creative_profile,
                        "source_grounded_result_first_cold_open": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "cold_open_must_be_replayed_after_causes": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "narration_budget_required": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "supplied_showrunner_plan_required_unchanged": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "retention_beats_required": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "information_states_required": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "character_state_deltas_for_diagnosed_changes": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "shot_intent_required": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "triggered_audio_beats_required": (
                            self.creative_profile == SHORT_DRAMA_PROFILE
                        ),
                        "ending_at_current_chapter_boundary": True,
                        "one_visible_speaker_per_turn": True,
                        "performance_plan_required": True,
                        "camera_plan_required": True,
                        "camera_mode_default": "locked",
                        "camera_move_requires_motivation": True,
                        "one_camera_trajectory_per_shot": True,
                        "dialogue_action_axis_locked": True,
                    },
                    **({"repair": repair} if repair else {}),
                },
            )
            try:
                plan = normalizer._validate_episode_data(data, episode, bible)
                if showrunner is not None:
                    plan = plan.model_copy(update={"showrunner_plan": showrunner})
                plan = apply_creative_direction(
                    plan,
                    diagnosis,
                    bible,
                    profile=self.creative_profile,
                )
                plan = normalize_chronological_plan(plan, diagnosis, episode)
                deterministic = evaluate_script_quality(
                    plan, diagnosis, episode, previous_state=previous_state
                )
                if not deterministic.passed:
                    raise ValueError(deterministic.model_dump_json())
                qualitative = _bounded_validate(
                    "review_episode",
                    self.max_revisions,
                    lambda review_repair: self._invoke(
                        "review_episode",
                        {
                            **common,
                            "chapter_diagnosis": diagnosis.model_dump(mode="json"),
                            "episode_plan": plan.model_dump(mode="json"),
                            "schema": ScriptQualityReport.model_json_schema(),
                            **({"repair": review_repair} if review_repair else {}),
                        },
                    ),
                    ScriptQualityReport.model_validate,
                )
                report = evaluate_script_quality(
                    plan,
                    diagnosis,
                    episode,
                    qualitative=qualitative,
                    previous_state=previous_state,
                )
                last_report = report
                if not report.passed:
                    raise ValueError(report.model_dump_json())
                state = _bounded_validate(
                    "update_series_state",
                    self.max_revisions,
                    lambda state_repair: self._invoke(
                        "update_series_state",
                        {
                            **common,
                            "chapter_diagnosis": diagnosis.model_dump(mode="json"),
                            "episode_plan": plan.model_dump(mode="json"),
                            "schema": SeriesState.model_json_schema(),
                            **({"repair": state_repair} if state_repair else {}),
                        },
                    ),
                    lambda value: validate_series_state(
                        SeriesState.model_validate(value), episode, previous_state
                    ),
                )
                return EpisodePlanningBundle(
                    diagnosis=diagnosis,
                    plan=plan,
                    quality_report=report,
                    updated_series_state=state,
                )
            except (ValidationError, ValueError) as error:
                if revision >= self.max_revisions:
                    break
                repair = {
                    "revision": revision + 1,
                    "previous_response": data,
                    "validation_errors": _validation_feedback(error),
                }
        detail = last_report.model_dump_json() if last_report else json.dumps(
            repair or {}, ensure_ascii=False
        )
        raise EpisodePlanningFailed(
            f"command planner script quality gate remained invalid: {detail}",
            episode_index=episode.index,
            failed_stage="episode_script_review",
            attempts=self.max_revisions + 1,
            elapsed_seconds=time.monotonic() - self._planning_started,
            intermediate_root=draft_root,
        )
