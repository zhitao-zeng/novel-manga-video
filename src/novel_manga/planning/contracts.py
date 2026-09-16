"""planning.contracts responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
from novel_manga.story.fields import actions_field
from novel_manga.story.fields import cast_field
from novel_manga.story.fields import extras_field
from novel_manga.story.fields import turn_field
import json
import novel_manga.planning.constants as pc_constants

def outline_schema(mode: str, segment_ids: list[str]) -> dict:
    sections = pc_constants.OUTLINE_SECTIONS[mode]
    return {
        "type": "object", "additionalProperties": False, "required": ["sections", "coverage"],
        "properties": {
            "sections": {"type": "object", "additionalProperties": False, "required": list(sections),
                         "properties": {name: {"type": "string", "minLength": 1} for name in sections}},
            "coverage": {"type": "array", "minItems": len(segment_ids), "maxItems": len(segment_ids), "items": {
                "type": "object", "additionalProperties": False, "required": ["segment_id", "placement"],
                "properties": {"segment_id": {"type": "string", "enum": segment_ids},
                               "placement": {"type": "string", "minLength": 1}}}},
        },
    }


def build_schema(character_names: list[str], location_names: list[str], segment_ids: list[str], *, ctx: PlannerContext) -> dict:
    cast_array = cast_field(character_names)
    turn = turn_field(character_names, ctx.anonymous_speakers, pc_constants.DELIVERY_MODES)
    stage = {
        "type": "object",
        "additionalProperties": False,
        "required": ["segment_id", "source_quote", "start_state", "event", "end_state", "camera", "light", "sfx", "shot_scale", "turns", "in_frame", "actions", "extras"],
        "properties": {
            "segment_id": {"type": "string", "enum": segment_ids},
            "source_quote": {"type": "string"},
            "start_state": {"type": "string"},
            "event": {"type": "string"},
            "end_state": {"type": "string"},
            "camera": {"type": "string"},
            "light": {"type": "string"},
            "sfx": {"type": "string"},
            "shot_scale": {"type": "string", "enum": pc_constants.SHOT_SCALES},
            "turns": {"type": "array", "minItems": 1, "maxItems": 8, "items": turn},
            # who is actually in the picture of this stage (a subset of clip.characters), and the actions as
            # actor / verb phrase / target - the renderer was given four "core subjects" and one sentence, and
            # picked two of them to kiss (雾月 761)
            "in_frame": cast_array,
            # unnamed people the passage puts in the picture, by a short description; they have no card
            "extras": extras_field(),
            "actions": actions_field(),
        },
    }
    clip = {
        "type": "object",
        "additionalProperties": False,
        "required": ["clip_id", "location", "characters", "avoid", "stages"],
        "properties": {
            "clip_id": {"type": "string"},
            "location": {"type": "string", "enum": location_names},
            # capped: an uncapped array let the model repeat one name until the token budget ran out
            "characters": cast_array,
            "avoid": {"type": "string"},
            "stages": {"type": "array", "minItems": 1, "maxItems": 6, "items": stage},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["video_title", "hook", "summary", "clips", "skipped_segments"],
        "properties": {
            "video_title": {"type": "string"},
            "hook": {"type": "string"},
            "summary": {"type": "string"},
            "clips": {"type": "array", "minItems": 1, "maxItems": 10, "items": clip},
            "skipped_segments": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["segment_id", "reason"],
                    "properties": {
                        "segment_id": {"type": "string", "enum": segment_ids},
                        "reason": {"type": "string"},
                    },
                },
            },
        },
    }


def validate_outline(content: str, mode: str, segment_ids: list[str]) -> list[str]:
    if not content.strip():
        return ["empty content; reasoning is not an outline"]
    try:
        data = json.loads(content)
    except ValueError:
        return ["outline content is not complete JSON"]
    if not isinstance(data, dict):
        return ["outline must be an object"]
    sections = data.get("sections")
    errors = []
    for name in pc_constants.OUTLINE_SECTIONS[mode]:
        text = sections.get(name) if isinstance(sections, dict) else None
        if not isinstance(text, str) or not text.strip():
            errors.append(f"missing outline section: {name}")
    coverage = data.get("coverage")
    if not isinstance(coverage, list):
        return [*errors, "missing source coverage"]
    ids = [item.get("segment_id") for item in coverage if isinstance(item, dict)]
    if len(ids) != len(segment_ids) or set(ids) != set(segment_ids):
        errors.append("source coverage must contain every segment exactly once")
    if any(not isinstance(item, dict) or not isinstance(item.get("placement"), str) or not item["placement"].strip() for item in coverage):
        errors.append("source coverage placement is empty")
    return errors
