"""In-memory handoffs between existing planning stages; no new files or formats."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from novel_manga.models.bible import StoryBible


@dataclass(frozen=True)
class ChapterSource:
    episode: Any
    novel_dir: Path
    episode_dir: Path
    part: int
    full_bible: StoryBible
    chapter_text: str
    profile: dict
    method: Any
    existing_draft: str | None
    genre: dict
    planning_budget: dict
    segments: list


@dataclass(frozen=True)
class ChapterCast:
    full_bible: StoryBible
    bible: StoryBible
    names: list
    everyone: list
    location_map: dict
    grammar: dict | None
    ledger_snapshot: dict | None
    identity_data: dict
    props_added_at: dict
    source_places: list
    recap_path: Path
    previous_recap: list
    previous_ending: dict | None
    previous_volumes: list
    sliced_characters: list
    sliced_locations: list


@dataclass(frozen=True)
class PlanningInputs:
    source: ChapterSource
    cast: ChapterCast
    bible: StoryBible
    names: list
    location_map: dict
    authored: dict | None
    prop_all: list
    prop_names: list
    schema: dict
    payload: dict


@dataclass(frozen=True)
class PlanningOutcome:
    result: tuple | None
    attempts: list
    last_issues: list
    final_errors: list
    posture_states: dict | None
    started: float
    gate_run: str
