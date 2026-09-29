"""An authored shot budgeted longer than a clip must reach the patch's split, not die on the way.

A draft bound from an authored sheet carries 预算秒 as duration_seconds, so budget.py sends any
over-long shot to STAGE_ABOVE_MAXIMUM and the patch loop asks the model for continuations.
_apply_patch expands the shot into parts, and keep_authored used to reject the whole proposal
outright ("the patch changed the number of stages") - every authored chapter with one long shot
burned its patch rounds and failed planning forever.  The parts now carry the model's directed
content with the author's identity stamped (part 1 keeps the shot id, later parts extend it as
镜号.vN, the same shape voice_splits writes into the sheet), keep_authored accepts exactly that
expansion, and any other shape change still refuses.
"""
from __future__ import annotations

import pytest

from novel_manga.application.planning import requests
from novel_manga.planning.binding import keep_authored


def turn(text, inner=False):
    row = {"speaker_name": "甲", "delivery_mode": "visible_dialogue", "text": text}
    if inner:
        row["inner_monologue"] = True
    return row


def stage(authored_id="1", seconds=40.0, event="长对白"):
    return {"segment_id": "seg_1", "source_quote": event, "start_state": "站着",
            "event": event, "end_state": "说完", "camera": "平拍", "light": "白天",
            "sfx": "", "shot_scale": "中景", "duration_seconds": seconds,
            "shot_id": authored_id, "authored_id": authored_id, "authored_seconds": seconds,
            "authored_angle": "平视", "scene_id": "s1", "in_frame": ["甲"],
            "turns": [turn("第一句很长的话"), turn("心里的想法")],
            "actions": [], "extras": []}


def authored_reply(first_event="第一镜", second_event="第二镜"):
    """What the patch model answers for one rejected authored shot: a stage plus one continuation.

    The reply schema never contains the authored columns, so the parts' identity and provenance
    are whatever _apply_patch stamps onto them.
    """
    def strip(stage_row):
        return {k: v for k, v in stage_row.items()
                if k not in ("shot_id", "authored_id", "authored_seconds", "authored_angle",
                             "scene_id", "duration_seconds")}
    first = {**strip(stage(event=first_event)), "turns": [turn("第一句很长的话")]}
    second = {**strip(stage(event=second_event)), "turns": [turn("心里的想法")]}
    return {"label": "clip_1 stage 1", "stage": first, "continuations": [second]}


def test_an_authored_split_survives_keep_authored():
    original = stage()
    raw = {"clips": [{"clip_id": "clip_1", "location": "教室", "stages": [dict(original)]}]}
    reply = {"insertions": [], "replacements": [authored_reply()]}
    patched = requests._apply_patch(raw, reply, {"clip_1 stage 1"}, authored=True)
    kept = keep_authored(raw, patched, split_labels={"clip_1 stage 1"})
    parts = kept["clips"][0]["stages"]
    # identity: part 1 keeps the author's shot number, the continuation extends it like the sheet does
    assert [s["shot_id"] for s in parts] == ["1", "1.v2"]
    assert [s["authored_id"] for s in parts] == ["1", "1.v2"]
    # content: the parts play what the patch directed, not the author's whole over-long shot
    assert [s["event"] for s in parts] == ["第一镜", "第二镜"]
    # the author's over-limit budget must not come back: it is the very defect being split
    assert "duration_seconds" not in parts[0] and "duration_seconds" not in parts[1]
    # provenance survives: the author's scene grouping and sheet columns stay on record
    assert all(s["scene_id"] == "s1" and s["authored_angle"] == "平视" for s in parts)


def test_the_split_expansion_without_the_split_labels_request_is_refused():
    original = stage()
    raw = {"clips": [{"clip_id": "clip_1", "location": "教室", "stages": [dict(original)]}]}
    reply = {"insertions": [], "replacements": [authored_reply()]}
    patched = requests._apply_patch(raw, reply, {"clip_1 stage 1"}, authored=True)
    with pytest.raises(ValueError, match="changed the number of stages"):
        keep_authored(raw, patched)  # the expansion was not in the patch request


def test_a_split_that_loses_authored_words_is_refused():
    original = stage()
    raw = {"clips": [{"clip_id": "clip_1", "location": "教室", "stages": [dict(original)]}]}
    reply = authored_reply()
    reply["continuations"][0]["turns"] = []  # the second line was dropped
    with pytest.raises(ValueError, match="voice split"):
        requests._apply_patch(raw, {"insertions": [], "replacements": [reply]}, {"clip_1 stage 1"}, authored=True)


def test_an_unstamped_extra_stage_is_refused():
    original = stage()
    raw = {"clips": [{"clip_id": "clip_1", "location": "教室", "stages": [dict(original)]}]}
    reply = authored_reply()
    patched = requests._apply_patch(raw, {"insertions": [], "replacements": [reply]}, {"clip_1 stage 1"}, authored=True)
    del patched["clips"][0]["stages"][1]["authored_id"]  # the lineage stamp is gone
    with pytest.raises(ValueError, match="changed the number of stages"):
        keep_authored(raw, patched, split_labels={"clip_1 stage 1"})


def test_a_removed_authored_stage_is_refused():
    original = stage()
    before = {"clips": [{"clip_id": "clip_1", "location": "教室", "stages": [original, stage("2")]}]}
    after = {"clips": [{"clip_id": "clip_1", "location": "教室", "stages": [original]}]}
    with pytest.raises(ValueError):
        keep_authored(before, after)


def test_an_identity_swap_across_stages_is_refused():
    before = {"clips": [{"clip_id": "clip_1", "location": "教室",
                         "stages": [stage("1"), stage("2")]}]}
    after = {"clips": [{"clip_id": "clip_1", "location": "教室",
                        "stages": [stage("2"), stage("1")]}]}
    with pytest.raises(ValueError, match="another shot's identity"):
        keep_authored(before, after)


def test_unchanged_shapes_still_restore_authored_columns():
    original = stage()
    rewritten = {k: v for k, v in stage().items()}
    rewritten["event"] = "模型只改了绑定字段"
    before = {"clips": [{"clip_id": "clip_1", "location": "教室", "stages": [original]}]}
    after = {"clips": [{"clip_id": "clip_1", "location": "教室", "stages": [rewritten]}]}
    kept = keep_authored(before, after)
    assert kept["clips"][0]["stages"][0]["event"] == "长对白"
