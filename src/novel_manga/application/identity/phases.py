"""Phase cards: a character whose look changes for good part-way through the book gets one card per phase,
and the chapter decides which card - and which description - a clip references.

series_assets/phases.json, per novel:

    {"policy": "phase-cards-v1",
     "characters": {"沈玄川": [
         {"from": 1406, "to": 3504, "asset_id": "character_001-p2", "label": "白发青年",
          "hair": "满头白发…", "appearance": "…", "base_costume": "…", "age": "青年"},
         {"from": 3505, "to": null, "asset_id": "character_001-p3", "label": "白发老年", "…": "…"}]}}

A chapter outside every listed range keeps the bible entry and the base card, so a novel without the file
plans, renders and reviews exactly as before.  A variant asset id is the base id plus "-p<n>"; it lives in
series_assets/characters/<asset_id>/ like any card (spec.json + turnaround.jpeg) and is drawn once by
build_phase_cards.py.  build_selected() and review_cards() walk the bible by index, so they never see a
variant - which keeps the change contained: only the plan (reference path + anchor text) and the clip review
(card text) look at the chapter, and the render follows the plan's paths as it always did.

Measured 2026-09-12 on 诸天万象录: 沈玄川 turns white-haired in ch1406 and stays so for the remaining 2400
chapters (63% of the book) while his one card showed the black-haired student of chapter 1; 阿曜 is an
unshapeshifted beast until ch3685 and his card is a man in a jacket.
"""
from __future__ import annotations

import json
from pathlib import Path

import novel_manga.episodes as ep_names

from novel_manga.models.bible import Character

POLICY = "phase-cards-v1"
# A phase may set any of these; a key present with "" clears the field (a beast has no hair style).
LOOK_FIELDS = ("hair", "appearance", "wardrobe", "base_costume", "silhouette", "palette", "age",
               "signature_prop", "visual_archetype")


def wearable_prop(phase: dict, bible) -> tuple | None:
    """(prop, prop_asset_id) for a phase whose look is a wearable prop ("wears": "Mark XLII 战甲"),
    else None.  The prop card is the armor's single source of truth; the wearer's phase card is
    drawn from it (build_phase_cards.py), and the clip references both."""
    name = str(phase.get("wears") or "").strip()
    if not name:
        return None
    for index, prop in enumerate(getattr(bible, "props", None) or [], start=1):
        if prop.name == name:
            return prop, f"prop_{index:03d}"
    return None


def load_phases(novel_dir: Path) -> dict[str, list[dict]]:
    """{character name: [phase, ...]} - {} when the novel has no phases.json or it is unreadable."""
    try:
        data = json.loads((novel_dir / "series_assets" / "phases.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    characters = data.get("characters") if isinstance(data, dict) else None
    return characters if isinstance(characters, dict) else {}


def phase_for(phases: dict[str, list[dict]], name: str, chapter: int | None) -> dict | None:
    """The phase covering this chapter (from/to inclusive, to=null open-ended), else None."""
    if chapter is None:
        return None
    for phase in phases.get(name) or []:
        start = int(phase.get("from") or 1)
        end = phase.get("to")
        if start <= chapter and (end is None or chapter <= int(end)):
            return phase
    return None


def phased(character: Character, phase: dict | None) -> Character:
    """The bible entry with the phase's look laid over it - a copy; the bible itself never changes.  A phase
    that names a costume also replaces `wardrobe`, the field the clip review reads out to the judge."""
    if not phase:
        return character
    overrides = {field: str(phase[field]) for field in LOOK_FIELDS if field in phase}
    if "base_costume" in overrides and "wardrobe" not in overrides:
        overrides["wardrobe"] = overrides["base_costume"]
    return character.model_copy(update=overrides)


def chapter_of(episode_dir: Path) -> int | None:
    """The chapter an episode directory covers; each part of a cut chapter answers the chapter, and wears its phase card."""
    parsed = ep_names.parse_episode(Path(episode_dir).name)
    return parsed[0] if parsed else None


def phase_card(novel_dir: Path, phases: dict[str, list[dict]], name: str, chapter: int | None) -> Path | None:
    """The phase's turnaround, relative to the novel dir, when this chapter has a phase and its card is drawn -
    what the clip review shows the judge instead of the base card the plan referenced."""
    phase = phase_for(phases, name, chapter)
    if not phase or not phase.get("asset_id"):
        return None
    relative = Path("series_assets") / "characters" / str(phase["asset_id"]) / "turnaround.jpeg"
    return relative if (novel_dir / relative).is_file() else None


def phase_labels(clips: list[dict]) -> list[str]:
    """'name:label' for every phase a plan's references use - the plan file says so, so a relaunch can tell
    plans made before phases.json existed from plans made after."""
    return sorted({f"{ref['name']}:{ref['phase']}" for clip in clips
                   for ref in clip.get("references", []) if ref.get("phase")})
