from __future__ import annotations

import re
from pathlib import Path

from .ingest import read_novel
from .models import (
    ChapterDiagnosis,
    EpisodePlan,
    ScriptQualityReport,
    SeriesState,
    StoryBible,
)
from .script_planning import (
    evaluate_script_quality,
    validate_chapter_diagnosis,
    validate_series_state,
)
from .util import atomic_write_json


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value)


def validate_planning_bundle(
    source: str | Path,
    bundle_dir: str | Path,
    *,
    novel_id: str,
    title: str | None = None,
    output: str | Path | None = None,
) -> dict:
    """Validate persisted planning artifacts without invoking a model or media provider."""

    novel = read_novel(source, novel_id=novel_id, title=title)
    root = Path(bundle_dir).resolve()
    bible = StoryBible.model_validate_json((root / "story_bible.json").read_text(encoding="utf-8"))
    known_characters = {character.name for character in bible.characters}

    episode_rows = []
    quote_total = 0
    quote_valid = 0
    visible_speaker_violations = []
    unknown_shot_characters = []
    total_shots = 0
    total_turns = 0
    total_script_chars = 0
    script_quality_failures = []
    previous_state: SeriesState | None = None

    for episode in novel.episodes:
        plan_path = root / f"episode_{episode.index:03d}_plan.json"
        diagnosis_path = root / f"episode_{episode.index:03d}_diagnosis.json"
        quality_path = root / f"episode_{episode.index:03d}_script_quality.json"
        state_path = root / f"episode_{episode.index:03d}_series_state.json"
        plan = EpisodePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
        diagnosis = validate_chapter_diagnosis(
            ChapterDiagnosis.model_validate_json(
                diagnosis_path.read_text(encoding="utf-8")
            ),
            episode,
            bible,
        )
        state = validate_series_state(
            SeriesState.model_validate_json(state_path.read_text(encoding="utf-8")),
            episode,
            previous_state,
        )
        stored_quality = ScriptQualityReport.model_validate_json(
            quality_path.read_text(encoding="utf-8")
        )
        quality = evaluate_script_quality(
            plan,
            diagnosis,
            episode,
            qualitative=stored_quality,
            previous_state=previous_state,
        )
        previous_state = state
        if not quality.passed:
            script_quality_failures.append(
                {
                    "episode": episode.index,
                    "issues": [issue.model_dump(mode="json") for issue in quality.issues],
                }
            )
        normalized_source = _normalized(episode.source_text)
        episode_quote_total = 0
        episode_quote_valid = 0
        episode_script_chars = 0

        for shot in plan.shots:
            total_shots += 1
            for quote in [shot.source_quote, *(turn.source_quote for turn in shot.turns)]:
                quote_total += 1
                episode_quote_total += 1
                normalized_quote = _normalized(quote)
                if normalized_quote and normalized_quote in normalized_source:
                    quote_valid += 1
                    episode_quote_valid += 1

            for character in shot.characters:
                if character not in known_characters:
                    unknown_shot_characters.append(
                        {"episode": episode.index, "shot": shot.index, "character": character}
                    )

            for turn_index, turn in enumerate(shot.turns, start=1):
                total_turns += 1
                turn_chars = len(_normalized(turn.text))
                total_script_chars += turn_chars
                episode_script_chars += turn_chars
                if turn.speaking and (
                    turn.role == "narrator"
                    or turn.speaker_name in {"", "旁白", "narrator"}
                    or turn.speaker_name not in known_characters
                ):
                    visible_speaker_violations.append(
                        {
                            "episode": episode.index,
                            "shot": shot.index,
                            "turn": turn_index,
                            "role": turn.role,
                            "speaker_name": turn.speaker_name,
                        }
                    )

        episode_rows.append(
            {
                "index": episode.index,
                "plan": plan_path.name,
                "schema_valid": True,
                "shot_count": len(plan.shots),
                "turn_count": sum(len(shot.turns) for shot in plan.shots),
                "script_chars": episode_script_chars,
                "source_chars": episode.text_count,
                "script_to_source_ratio": round(episode_script_chars / max(1, episode.text_count), 6),
                "source_quote_valid_ratio": round(
                    episode_quote_valid / max(1, episode_quote_total), 6
                ),
                "script_quality_passed": quality.passed,
                "critical_event_coverage": quality.critical_event_coverage,
            }
        )

    source_quote_valid_ratio = round(quote_valid / max(1, quote_total), 6)
    report = {
        "schema_version": 1,
        "passed": (
            source_quote_valid_ratio == 1.0
            and not visible_speaker_violations
            and not unknown_shot_characters
            and not script_quality_failures
        ),
        "story_bible_schema_valid": True,
        "episode_plan_schema_valid": True,
        "source_quote_valid_ratio": source_quote_valid_ratio,
        "visible_speaker_violations": visible_speaker_violations,
        "unknown_shot_characters": unknown_shot_characters,
        "script_quality_failures": script_quality_failures,
        "shot_count": total_shots,
        "turn_count": total_turns,
        "script_chars": total_script_chars,
        "episodes": episode_rows,
    }
    output_path = Path(output).resolve() if output else root / "planning_validation.json"
    atomic_write_json(output_path, report)
    return {**report, "report": str(output_path)}
