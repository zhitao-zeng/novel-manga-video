from __future__ import annotations

import re
from dataclasses import dataclass

from .models import EpisodePlan, ScriptTurn, StoryBible
from .planner import OpenAICompatiblePlanner


def normalize_reuse_text(value: str) -> str:
    return "".join(
        character
        for character in value.casefold()
        if character.isalnum() or "\u3400" <= character <= "\u9fff"
    )


def _aliases(value: str) -> set[str]:
    return {
        normalize_reuse_text(item)
        for item in re.split(r"[/／|、]", value)
        if normalize_reuse_text(item)
    }


def _speakers_compatible(left: str, right: str) -> bool:
    return bool(_aliases(left) & _aliases(right))


@dataclass(frozen=True)
class PlanUnit:
    unit_id: str
    shot_index: int
    turn_index: int
    turn: ScriptTurn
    source_quote: str


def flatten_plan(plan: EpisodePlan) -> list[PlanUnit]:
    units = []
    for shot in plan.shots:
        turns = shot.turns or [
            ScriptTurn(text=shot.subtitle, source_quote=shot.source_quote)
        ]
        for turn_index, turn in enumerate(turns, start=1):
            units.append(
                PlanUnit(
                    unit_id=f"shot_{shot.index:03d}_turn_{turn_index:02d}",
                    shot_index=shot.index,
                    turn_index=turn_index,
                    turn=turn,
                    source_quote=turn.source_quote or shot.source_quote,
                )
            )
    return units


def canonicalize_plan_to_bible(plan: EpisodePlan, bible: StoryBible) -> EpisodePlan:
    normalizer = object.__new__(OpenAICompatiblePlanner)
    return normalizer._canonicalize_characters(plan, bible)


def _reuse_score(target: PlanUnit, source: PlanUnit) -> tuple[int, str] | None:
    if target.turn.speaking != source.turn.speaking:
        return None
    target_text = normalize_reuse_text(target.turn.text)
    source_text = normalize_reuse_text(source.turn.text)
    target_quote = normalize_reuse_text(target.source_quote)
    source_quote = normalize_reuse_text(source.source_quote)
    quote_equal = bool(target_quote and target_quote == source_quote)

    if target.turn.speaking:
        if target_text != source_text or not _speakers_compatible(
            target.turn.speaker_name, source.turn.speaker_name
        ):
            return None
        return (100 + int(quote_equal) * 10, "exact_visible_dialogue")

    if target_text == source_text:
        return (90 + int(quote_equal) * 10, "exact_narration")
    text_contained = bool(
        target_text
        and source_text
        and (target_text in source_text or source_text in target_text)
    )
    anchored = bool(
        target_text
        and source_text
        and (
            target_text in source_quote
            or source_text in target_quote
            or target_text in target_quote
        )
    )
    if quote_equal:
        return (80 + int(text_contained) * 5, "grounded_narration_rewrite")
    if text_contained and anchored:
        return (70, "grounded_narration_excerpt")
    return None


def match_reusable_units(target: EpisodePlan, source: EpisodePlan) -> list[dict]:
    """Find a one-to-one, source-grounded visual reuse mapping.

    Visible dialogue is reusable only when speaker and locked text are exact.
    Narration may be a grounded compression because its visual clip must keep
    every on-screen mouth closed.
    """

    source_units = flatten_plan(source)
    used: set[str] = set()
    matches = []
    for target_unit in flatten_plan(target):
        candidates = []
        for source_unit in source_units:
            if source_unit.unit_id in used:
                continue
            scored = _reuse_score(target_unit, source_unit)
            if scored is not None:
                candidates.append((scored[0], source_unit, scored[1]))
        if not candidates:
            raise ValueError(f"no safe reusable source for {target_unit.unit_id}")
        candidates.sort(key=lambda item: (-item[0], item[1].shot_index, item[1].turn_index))
        score, source_unit, match_type = candidates[0]
        used.add(source_unit.unit_id)
        matches.append(
            {
                "target_unit_id": target_unit.unit_id,
                "source_unit_id": source_unit.unit_id,
                "target_shot_index": target_unit.shot_index,
                "source_shot_index": source_unit.shot_index,
                "speaking": target_unit.turn.speaking,
                "match_type": match_type,
                "score": score,
                "text_equal": normalize_reuse_text(target_unit.turn.text)
                == normalize_reuse_text(source_unit.turn.text),
                "source_quote_equal": normalize_reuse_text(target_unit.source_quote)
                == normalize_reuse_text(source_unit.source_quote),
                "target_text": target_unit.turn.text,
                "source_text": source_unit.turn.text,
            }
        )
    return matches
