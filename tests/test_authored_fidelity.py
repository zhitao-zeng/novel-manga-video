"""What the author wrote has to arrive, and the gate that says so has to be reading it.

The binder's rule was always "the model is never asked for what the author wrote".  It was applied to
three of the nine columns.  场景 and 台词 / 声音 are authored too - the brief gives them an exact form and
the audit checks that form - yet they were asked for outright, so a legal, checked cell could still be
replaced before it reached the video.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from novel_manga.planning.audit import Audit, check_clip_plan
from novel_manga.planning.binding import bind_schema, merge, settled_names, written_names
from novel_manga.planning.storyboard import authored_sound
from novel_manga.planning.validation import flatten_clips
from novel_manga.application.packing.context import compiler_options
from novel_manga.story.compilation import ClipCompiler


def shot(shot_id="A12", *, sound="", place="书房", seconds=12, angle="低机位仰拍"):
    return {"镜号": shot_id, "摄影角度": angle, "景别": "近景", "画面内容 / 动作": "梁舟站起身",
            "场景": place, "台词 / 声音": sound, "机位 / 运镜 / 连续性": "固定机位",
            "叙事目的": "转折", "预算秒": seconds}


SOUND = '梁舟（内心独白、压低声音）：“我不能走。”\n声音：风声'


def binding_answer(**extra):
    return {"video_title": "t", "hook": "h", "summary": "s", "skipped_segments": [],
            "speaker_names": [{"written": "梁舟", "name": "梁舟"}],
            "location_names": [{"written": "书房", "name": "书房"}],
            "bindings": [{"镜号": "A12", "segment_id": "seg_1", "source_quote": "他站了起来",
                          "start_state": "梁舟坐着", "end_state": "梁舟站着", "light": "暖黄台灯",
                          "in_frame": ["梁舟"], "extras": [], "actions": []}],
            **extra}


# --- the column is parsed, not re-invented -------------------------------------------------------

def test_the_dialogue_cell_parses_into_speaker_manner_and_words():
    parsed = authored_sound(SOUND)
    assert parsed.turns == ({"written_speaker": "梁舟", "delivery_mode": "offscreen_dialogue",
                             "emotion": "压低声音", "text": "我不能走。"},)
    assert parsed.sfx == "风声"
    assert parsed.problems == ()


def test_an_illegal_delivery_or_shape_is_reported_rather_than_guessed():
    assert authored_sound("梁舟（喊）：“走！”").problems[0][1].startswith("发声方式写的是")
    assert authored_sound("梁舟: 走").problems[0][1].startswith("要写成")


def test_the_model_is_not_asked_about_anything_the_catalogue_already_answers():
    """梁舟 is a bible character spelled exactly that way.  Asking anyway is how it could come back as
    somebody else: the field was an enum of every available name and the binder took the answer."""
    authored = {"shots": [shot(sound=SOUND)]}
    assert written_names(authored) == (["梁舟"], ["书房"])
    schema = bind_schema(authored, ["梁舟"], ["书房", "庭院"], ["seg_1"],
                         ctx=SimpleNamespace(anonymous_speakers=()))
    per_shot = schema["properties"]["bindings"]["items"]["properties"]
    assert "turns" not in per_shot and "sfx" not in per_shot and "location" not in per_shot
    assert "location_names" not in schema["properties"]
    assert schema["properties"]["speaker_names"]["maxItems"] == 0     # nothing left to ask
    assert settled_names(["梁舟", "小贩"], ["梁舟"]) == ({"梁舟": "梁舟"}, ["小贩"])


def test_a_speaker_the_catalogue_does_not_know_is_still_a_question():
    """阿甲 is not a bible name; the sheet writes 蝙蝠侠 and 学生甲 for real reasons."""
    schema = bind_schema({"shots": [shot(sound='阿甲（说）：“走。”')]}, ["梁舟"], ["书房"], ["seg_1"],
                         ctx=SimpleNamespace(anonymous_speakers=()))
    assert schema["properties"]["speaker_names"]["maxItems"] == 1


def test_an_exact_name_cannot_be_answered_away():
    merged = merge({"shots": [shot(sound=SOUND)]},
                   binding_answer(speaker_names=[{"written": "梁舟", "name": "周衡"}]),
                   character_names=["梁舟", "周衡"])
    assert merged["clips"][0]["stages"][0]["turns"][0]["speaker_name"] == "梁舟"


def test_the_binder_cannot_move_the_scene_or_rewrite_the_line():
    """The whole finding in one case: given an answer that relocates and re-voices the shot, the
    author's own cell is what comes out."""
    merged = merge({"shots": [shot(sound=SOUND)]}, binding_answer(), character_names=["梁舟"])
    stage = merged["clips"][0]["stages"][0]
    assert merged["clips"][0]["location"] == "书房"
    assert stage["sfx"] == "风声"
    assert stage["turns"] == [{"speaker_name": "梁舟", "delivery_mode": "offscreen_dialogue",
                               "text": "我不能走。", "emotion": "压低声音", "chat_target": ""}]


def test_a_speaker_the_binding_never_named_stops_the_chapter():
    with pytest.raises(ValueError, match="梁舟"):
        merge({"shots": [shot(sound=SOUND)]}, binding_answer(speaker_names=[]), character_names=[])


def test_a_scene_goes_where_the_author_put_it_whatever_the_model_answers():
    """The brief tells the author to copy a bible location exactly and the audit checks it, so there
    is nothing here for a model to decide.  Normalising 书房 to 书房（宅邸） and moving it to 庭院 are
    the same act from the binder's side - both replace the author's own word - so neither happens."""
    answer = binding_answer(location_names=[{"written": "书房", "name": "庭院"}])
    answer["bindings"] = [{**answer["bindings"][0], "镜号": n} for n in ("A1", "A2")]
    merged = merge({"shots": [shot("A1", sound=SOUND), shot("A2", sound=SOUND)]}, answer,
                   character_names=["梁舟"])
    assert [c["location"] for c in merged["clips"]] == ["书房"]


def test_a_scene_name_no_location_answers_to_is_a_proposal_not_a_relocation():
    """The skill's own 新增地点.md exists for this: chapter 10 of 在美漫当心灵导师的日子 needed a
    classroom that only enters the bible at chapter 44, and the agent refused to call it the coffee
    shop.  Handing it a list without the classroom would have made the pipeline do exactly that."""
    with pytest.raises(ValueError, match="哥谭大学教室"):
        bind_schema({"shots": [shot(sound=SOUND, place="哥谭大学教室")]}, ["梁舟"], ["书房", "庭院"],
                    ["seg_1"], ctx=SimpleNamespace(anonymous_speakers=()))


def test_an_unreadable_dialogue_cell_stops_the_chapter_instead_of_reaching_the_model():
    with pytest.raises(ValueError, match="A12"):
        merge({"shots": [shot(sound="梁舟（喊）：“走！”")]}, binding_answer(), character_names=["梁舟"])


# --- the planned length, shot number and angle survive -------------------------------------------

def test_the_authored_number_length_and_angle_reach_the_flat_shot():
    merged = merge({"shots": [shot(sound=SOUND)]}, binding_answer(), character_names=["梁舟"])
    flat = flatten_clips(merged)[0]
    assert flat["authored_id"] == "A12"
    assert flat["authored_seconds"] == 12
    assert flat["authored_angle"] == "低机位仰拍"
    assert flat["duration_seconds"] == 12.0
    assert "低机位仰拍" in flat["camera"] and "固定机位" in flat["camera"]


def test_a_planned_length_is_used_whoever_planned_it():
    """The estimator asked for a scene_id as well, which only the local scene method sets - so an
    authored 12-second action shot was estimated as a generic silent stage instead."""
    compiler = ClipCompiler(compiler_options())
    authored = {"turns": [], "duration_seconds": 12}
    assert compiler.shot_seconds(authored) == 12.0
    assert compiler.shot_seconds({"turns": []}) != 12.0


# --- the gate reads the fields the plan actually has ----------------------------------------------

def clip_plan(tmp_path, **clip):
    episode = tmp_path / "book_1"
    episode.mkdir(parents=True, exist_ok=True)
    (episode / "clip_plan.json").write_text(json.dumps(
        {"limits": {"max_clip_seconds": 30}, "clips": [
            {"kind": "video", "clip_id": "clip_01", "location": "书房", **clip}]}, ensure_ascii=False),
        encoding="utf-8")
    audit = Audit()
    check_clip_plan(tmp_path, {"locations": ["书房：一间书房"], "characters": [{"name": "梁舟"}]}, audit)
    return [f"{f.level}:{f.rule}" for f in audit.findings]


def test_an_uncarded_name_and_an_impossible_length_are_reported(tmp_path):
    """Both of these used to pass: the check read `characters` and `seconds`, and a clip plan has
    neither, so it looked at empty values and left a record saying it had looked."""
    found = clip_plan(tmp_path, cast=["没有建档的人"], request_seconds=99)
    assert "错:角色不在圣经里" in found
    assert "错:时长超出可渲染范围" in found


def test_a_clip_missing_those_fields_is_reported_rather_than_skipped(tmp_path):
    found = clip_plan(tmp_path)
    assert "错:镜头没有演员表字段" in found and "错:镜头没有时长字段" in found


def test_a_legal_clip_is_quiet(tmp_path):
    assert clip_plan(tmp_path, cast=["梁舟"], request_seconds=20) == []


def test_the_window_comes_from_the_plan_not_from_the_local_h3(tmp_path):
    """20 seconds is fine for an sd2.5 plan and impossible for a 15 s H3 one; the plan says which."""
    episode = tmp_path / "book_1"
    episode.mkdir(parents=True)
    (episode / "clip_plan.json").write_text(json.dumps(
        {"limits": {"max_clip_seconds": 15}, "clips": [
            {"kind": "video", "clip_id": "clip_01", "location": "书房", "cast": ["梁舟"],
             "request_seconds": 20}]}, ensure_ascii=False), encoding="utf-8")
    audit = Audit()
    check_clip_plan(tmp_path, {"locations": ["书房：一间书房"], "characters": [{"name": "梁舟"}]}, audit)
    assert [f.rule for f in audit.findings] == ["时长超出可渲染范围"]


def test_a_spelling_an_author_really_used_is_read_as_the_label_it_means():
    """第 11 集 of 在美漫当心灵导师的日子, the first sheet the sandbox wrote with nobody watching, has
    画外音 three times and 画外 three times.  Refusing the short form kept three lines out of the film
    and sent the whole chapter to a person over one missing character."""
    parsed = authored_sound("戈登（画外、由远及近）：“怪不得！低楼层的住户几乎全没了——”\n"
                            "蝙蝠侠（画外音、低）：“……对不起，教授。”")
    assert parsed.problems == ()
    assert [t["delivery_mode"] for t in parsed.turns] == ["offscreen_dialogue", "offscreen_dialogue"]
    assert [t["emotion"] for t in parsed.turns] == ["由远及近", "低"]


def test_a_label_nobody_has_been_seen_to_write_is_still_reported():
    """The list is of what has been observed, not of what might be meant."""
    assert authored_sound("戈登（旁白）：“三年前。”").problems[0][1].startswith("发声方式写的是")


def coverage(cited, *, authored, skipped=()):
    from novel_manga.planning.context import PlannerContext
    from novel_manga.planning.source_checks import chapter_coverage
    ctx = PlannerContext.from_env()
    ctx.authored_storyboard, ctx.max_skipped = authored, 0
    segments = [{"segment_id": "seg_7", "text": "蝙蝠侠站在阴影里想，他要永远杜绝这种可能的发生。"},
                {"segment_id": "seg_8", "text": "漫画里，蝙蝠侠不杀人这个设定，似乎从开始就存在了。"}]
    raw, errors, warnings = {"skipped_segments": [{"segment_id": s, "reason": "x"} for s in skipped]}, [], []
    chapter_coverage(raw, [{"turns": []}], segments, set(cited), "".join(s["text"] for s in segments),
                     ctx, errors, warnings)
    return raw, errors, warnings


def test_a_segment_left_uncited_by_a_reassignment_is_the_authors_omission_not_a_gap():
    """第 11 集: shot 13 was bound to seg_8, its quote was really in seg_7 and validation moved it there.
    seg_8 was then cited by nobody - but the rule that records an omission had already run, on the
    citations the model claimed, and seen seg_8 taken.  So the coverage gate called it a gap and the
    patch round wrote a fourteenth shot, in the author's voice, that the author never wrote."""
    raw, errors, warnings = coverage({"seg_7"}, authored=True)
    assert errors == []
    assert raw["skipped_segments"] == [{"segment_id": "seg_8", "reason": "作者的分镜没有取用这一段"}]
    assert any("seg_8" in w and "改编取舍" in w for w in warnings)


def test_the_ordinary_planner_is_still_held_to_covering_every_segment():
    _, errors, _ = coverage({"seg_7"}, authored=False)
    assert [issue.segment_id for issue in errors] == ["seg_8"]


def test_an_omission_already_on_record_is_not_recorded_twice():
    raw, errors, _ = coverage({"seg_7"}, authored=True, skipped=["seg_8"])
    assert errors == [] and [row["segment_id"] for row in raw["skipped_segments"]] == ["seg_8"]
