"""Bind an authored storyboard instead of re-planning the chapter.

planning/storyboard.py imports an authored sheet faithfully and marks it imported_needs_bindings: the
cuts are there, the production fields are empty, and its docstring says compilation refuses the draft
until a binding step exists.  This is that step.

The first attempt handed the sheet to the planner inside the request and told it to keep the cuts.  It
did not: 14 authored shots came back as 12, one shot scale out of twelve survived, and the authored
motion text was 4-16% similar to what came back.  An instruction in the payload cannot compete with a
schema that lets the model write any storyboard it likes.

So the model is not given the authored fields to write.  It answers one object per authored shot with
only the fields the import left empty, and the authored 画面内容 / 动作, 景别, 机位 and 预算秒 are merged
in afterwards, in code.  What the author wrote cannot be rewritten because it is never asked for.
"""
from __future__ import annotations

from novel_manga.story.fields import actions_field, cast_field, extras_field, turn_field
import novel_manga.planning.constants as pc_constants


def bind_schema(authored: dict, character_names: list[str], location_names: list[str],
                segment_ids: list[str], *, ctx) -> dict:
    """One answer per authored shot, in sheet order, carrying only what the import left empty."""
    ids = [str(shot["镜号"]) for shot in authored["shots"]]
    turn = turn_field(character_names, ctx.anonymous_speakers, pc_constants.DELIVERY_MODES)
    bound = {
        "type": "object",
        "additionalProperties": False,
        "required": ["镜号", "segment_id", "source_quote", "location", "start_state", "end_state",
                     "light", "sfx", "turns", "in_frame", "extras", "actions"],
        "properties": {
            "镜号": {"type": "string", "enum": ids},
            "segment_id": {"type": "string", "enum": segment_ids},
            "source_quote": {"type": "string"},
            "location": {"type": "string", "enum": location_names},
            "start_state": {"type": "string"},
            "end_state": {"type": "string"},
            "light": {"type": "string"},
            "sfx": {"type": "string"},
            "turns": {"type": "array", "maxItems": 8, "items": turn},
            "in_frame": cast_field(character_names),
            "extras": extras_field(),
            "actions": actions_field(),
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["video_title", "hook", "summary", "bindings", "skipped_segments"],
        "properties": {
            "video_title": {"type": "string"},
            "hook": {"type": "string"},
            "summary": {"type": "string"},
            # exactly as many as the sheet has, so a shot can be neither dropped nor invented
            "bindings": {"type": "array", "minItems": len(ids), "maxItems": len(ids), "items": bound},
            "skipped_segments": {
                "type": "array", "maxItems": 8,
                "items": {"type": "object", "additionalProperties": False,
                          "required": ["segment_id", "reason"],
                          "properties": {"segment_id": {"type": "string", "enum": segment_ids},
                                         "reason": {"type": "string"}}},
            },
        },
    }


def merge(authored: dict, answer: dict) -> dict:
    """The planner's own clips/stages shape, with the authored columns put back verbatim.

    Consecutive shots that bound to the same location become one clip, which is how the packer expects
    a scene to arrive; the authored order is never changed.
    """
    by_id = {str(b["镜号"]): b for b in answer.get("bindings", [])}
    clips: list[dict] = []
    for shot in authored["shots"]:
        shot_id = str(shot["镜号"])
        bound = by_id.get(shot_id)
        if bound is None:  # the schema forbids it, but a missing shot must not become a silent gap
            raise ValueError(f"binding is missing authored shot {shot_id}")
        stage = {
            "segment_id": bound["segment_id"],
            "source_quote": bound["source_quote"],
            "start_state": bound["start_state"],
            # the author's own columns, untouched
            "event": shot["画面内容 / 动作"],
            "shot_scale": shot["景别"],
            "camera": shot["机位 / 运镜 / 连续性"],
            "end_state": bound["end_state"],
            "light": bound["light"],
            "sfx": bound["sfx"],
            "turns": bound["turns"],
            "in_frame": bound["in_frame"],
            "extras": bound["extras"],
            "actions": bound["actions"],
            "authored_id": shot_id,
            "authored_seconds": shot["预算秒"],
            "authored_angle": shot["摄影角度"],
        }
        if clips and clips[-1]["location"] == bound["location"]:
            clips[-1]["stages"].append(stage)
        else:
            clips.append({"clip_id": f"clip_{len(clips) + 1}", "location": bound["location"],
                          "characters": [], "avoid": "", "stages": [stage]})
    for clip in clips:
        seen: list[str] = []
        for stage in clip["stages"]:
            for name in stage["in_frame"]:
                if name not in seen:
                    seen.append(name)
        clip["characters"] = seen[:6]
    return {
        "video_title": answer.get("video_title", ""),
        "hook": answer.get("hook", ""),
        "summary": answer.get("summary", ""),
        "clips": clips,
        "skipped_segments": answer.get("skipped_segments", []),
    }
