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

That rule was applied to three columns and not to the other two.  场景 and 台词 / 声音 are authored too -
the brief gives the author an exact, machine-readable form for them and the audit checks it - yet the
schema asked the model for `location`, `turns` and `sfx` outright, so it could still decide where the
scene happens, what is said, and how.  Both columns are now parsed (planning/storyboard.authored_sound),
and the model answers only what the sheet genuinely cannot settle.

That turned out to be less than it was first given.  Asking which cast member a written speaker is
meant asking about 席勒 too, whose name matches a bible character exactly - and an answer of 周衡 was
accepted, because the field was an enum of every available name.  A name the catalogue already
answers is not a question, so only aliases and short forms are asked now.  Scene names are not asked
at all: the brief tells the author to copy a bible location exactly, so a name that matches nothing
is a proposal for a new place - the skill writes one - and never a licence to move the scene to the
nearest room on the list, which is precisely what that proposal was written to avoid.
"""
from __future__ import annotations

from novel_manga.planning.storyboard import authored_sound
from novel_manga.story.fields import actions_field, cast_field, extras_field, speaker_field
import novel_manga.planning.constants as pc_constants


def written_names(authored: dict) -> tuple[list[str], list[str]]:
    """The distinct speaker names and scene names the author typed, in sheet order."""
    speakers, places = [], []
    for shot in authored["shots"]:
        for name in authored_sound(shot.get("台词 / 声音", "")).speakers:
            if name not in speakers:
                speakers.append(name)
        place = str(shot.get("场景") or "").strip()
        if place and place not in places:
            places.append(place)
    return speakers, places


def settled_names(written: list[str], available) -> tuple[dict, list[str]]:
    """Split what the author wrote into what the catalogue already settles and what is a question.

    A written name that exactly matches an available one is not a judgement call - 席勒 is 席勒 - and
    asking anyway is how 席勒 could come back as 周衡: the model was free to answer with any name on
    the list and the binder took it.  Only aliases, short forms and genuinely ambiguous references are
    questions, and only those are asked.
    """
    known = set(available)
    settled = {name: name for name in written if name in known}
    return settled, [name for name in written if name not in settled]


def unplaceable(authored: dict, location_names) -> list[str]:
    """Scene names the author wrote that no bible location answers to.

    The brief tells the author to copy a location name exactly, and the audit checks it, so a name
    that matches nothing is either a new place the story needs or a typo - never an invitation to put
    the scene somewhere else.  The agent's own 新增地点.md proposal exists for the first case; picking
    the nearest available room instead is the defect that proposal was written to avoid.
    """
    known = set(location_names)
    return [place for place in written_names(authored)[1] if place not in known]


def bind_schema(authored: dict, character_names: list[str], location_names: list[str],
                segment_ids: list[str], *, ctx) -> dict:
    """One answer per authored shot, in sheet order, carrying only what the import left empty."""
    ids = [str(shot["镜号"]) for shot in authored["shots"]]
    speakers, places = written_names(authored)
    # What the catalogue already answers is not asked.  Locations are never asked: the brief tells the
    # author to copy a bible name exactly, so a name that matches nothing is a proposal for a new place
    # (the skill writes one), not a licence to move the scene to the nearest room on the list.
    _, speakers = settled_names(speakers, character_names)
    missing = unplaceable(authored, location_names)
    if missing:
        raise ValueError("分镜表里的这些场景名，圣经里没有对应地点：" + "、".join(missing)
                         + "。先把地点补进圣经（agent 的新增地点提案就是为此），或改正表里的写法；"
                         "不能把这几镜挪到别的地方去。")
    bound = {
        "type": "object",
        "additionalProperties": False,
        "required": ["镜号", "segment_id", "source_quote", "start_state", "end_state",
                     "light", "in_frame", "extras", "actions"],
        "properties": {
            "镜号": {"type": "string", "enum": ids},
            "segment_id": {"type": "string", "enum": segment_ids},
            "source_quote": {"type": "string"},
            "start_state": {"type": "string"},
            "end_state": {"type": "string"},
            "light": {"type": "string"},
            "in_frame": cast_field(character_names),
            "extras": extras_field(),
            "actions": actions_field(),
        },
    }
    # One answer per distinct written name, not per shot: the same 秦宇 cannot be one person in shot 3
    # and another in shot 7, and a scene name cannot drift between shots of the same place.
    def naming(written: list[str], field: dict) -> dict:
        return {"type": "array", "minItems": len(written), "maxItems": len(written),
                "items": {"type": "object", "additionalProperties": False,
                          "required": ["written", "name"],
                          "properties": {"written": {"type": "string", "enum": written or [""]},
                                         "name": field}}}

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["video_title", "hook", "summary", "bindings", "skipped_segments",
                     "speaker_names"],
        "properties": {
            "video_title": {"type": "string"},
            "hook": {"type": "string"},
            "summary": {"type": "string"},
            "speaker_names": naming(speakers, speaker_field(character_names, ctx.anonymous_speakers)),
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


def merge(authored: dict, answer: dict, *, character_names=()) -> dict:
    """The planner's own clips/stages shape, with the authored columns put back verbatim.

    Consecutive shots that bound to the same location become one clip, which is how the packer expects
    a scene to arrive; the authored order is never changed.
    """
    by_id = {str(b["镜号"]): b for b in answer.get("bindings", [])}
    settled, _ = settled_names(written_names(authored)[0], character_names)
    # the catalogue's own answers last, so nothing in the reply can override a name that was never a
    # question: 席勒 is 席勒 whatever comes back
    speaker_of = {**{str(r["written"]): str(r["name"]) for r in answer.get("speaker_names", [])}, **settled}
    clips: list[dict] = []
    for shot in authored["shots"]:
        shot_id = str(shot["镜号"])
        bound = by_id.get(shot_id)
        if bound is None:  # the schema forbids it, but a missing shot must not become a silent gap
            raise ValueError(f"binding is missing authored shot {shot_id}")
        sound = authored_sound(shot.get("台词 / 声音", ""))
        if sound.problems:
            raise ValueError(f"authored shot {shot_id} has an unreadable 台词 / 声音 line: "
                             + "；".join(f"{line} —— {why}" for line, why in sound.problems))
        turns = []
        for turn in sound.turns:
            written = turn["written_speaker"]
            if written not in speaker_of:
                raise ValueError(f"binding did not say who {written!r} is (authored shot {shot_id})")
            turns.append({"speaker_name": speaker_of[written], "delivery_mode": turn["delivery_mode"],
                          "text": turn["text"], "emotion": turn["emotion"], "chat_target": ""})
        # the author's own scene name, not a name a model chose for it
        location = str(shot.get("场景") or "").strip()
        stage = {
            "segment_id": bound["segment_id"],
            "source_quote": bound["source_quote"],
            "start_state": bound["start_state"],
            # the author's own columns, untouched
            "event": shot["画面内容 / 动作"],
            "shot_scale": shot["景别"],
            # 摄影角度 is its own column and had nowhere to go: the sheet says 低机位仰拍 and only the
            # 机位 / 运镜 column was carried, so the angle the author chose never reached the picture.
            "camera": "；".join(p for p in (str(shot.get("摄影角度") or "").strip(),
                                            shot["机位 / 运镜 / 连续性"]) if p),
            # the planned length, under the name the estimator and the packer already read
            "duration_seconds": float(shot["预算秒"]),
            "shot_id": shot_id,
            "end_state": bound["end_state"],
            "light": bound["light"],
            # written by the author too, parsed rather than asked for
            "sfx": sound.sfx,
            "turns": turns,
            "in_frame": bound["in_frame"],
            "extras": bound["extras"],
            "actions": bound["actions"],
            "authored_id": shot_id,
            "authored_seconds": shot["预算秒"],
            "authored_angle": shot["摄影角度"],
        }
        if clips and clips[-1]["location"] == location:
            clips[-1]["stages"].append(stage)
        else:
            clips.append({"clip_id": f"clip_{len(clips) + 1}", "location": location,
                          "characters": [], "avoid": "", "stages": [stage]})
    for index, clip in enumerate(clips, 1):
        # The marker everything downstream reads as "a person cut this chapter": it decides whether the
        # per-shot durations reach H3 as shot_timing, whether the medium sentence is written at all,
        # whether in_frame is taken as authoritative rather than topped up by a scan, and which wording
        # the garment retention uses.  An authored sheet is exactly that, and it was the one directed
        # path that did not say so - so its 预算秒 stopped at the packer and never reached the request.
        clip["scene_id"] = f"scene_{index:02d}"
        seen: list[str] = []
        for stage in clip["stages"]:
            stage["scene_id"] = clip["scene_id"]
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
