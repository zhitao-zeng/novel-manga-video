"""One authored sheet, traced through every layer to the request that is actually sent.

The rule the binder was built on - the model is never asked for what the author wrote - can only be
checked where the author's words end up, not where they are stored.  A field can be kept by the
binder and dropped by the next step, which is what happened to 镜号, 预算秒 and 摄影角度; and the
per-shot durations reached the packer and stopped, because the marker that says "a person cut this"
was never set on this path.  The local scene method's own end-to-end test could not see any of it.

So: a fixed sheet, and at each layer the same question - is this still what the author wrote?
"""
from __future__ import annotations

import json

import pytest

from novel_manga.application.packing.context import load_context
from novel_manga.application.packing.service import compile_plan
from novel_manga.application.rendering import h3
from novel_manga.models.bible import StoryBible
from novel_manga.planning.binding import merge
from novel_manga.planning.budget import configure_budget
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.storyboard import authored_sound
from novel_manga.planning.validation import validate_and_normalize
from novel_manga.story.h3 import request_issues

SEGMENTS = [{"segment_id": "seg_1", "text": "梁舟把背包拖上溪岸，低头检查湿透的背带。"},
            {"segment_id": "seg_2", "text": "灰色山羊冲向梁舟，他举起木棍横挡，山羊撞上木棍。"}]

BIBLE = {"novel_title": "测试", "genre": "generic", "visual_style": "统一3D动画", "palette": "自然色",
         "style_fingerprint": "fixture",
         "characters": [{"name": "梁舟", "appearance": "黑发青年", "wardrobe": "青衣"}],
         "locations": ["溪岸：溪流与岩石"]}

# The nine columns, as an author fills them in.
SHEETS = [
    {"镜号": "A1", "摄影角度": "低机位仰拍", "景别": "中景", "场景": "溪岸",
     "画面内容 / 动作": "梁舟把背包拖上岸，坐下查看背带。",
     "台词 / 声音": '梁舟（说、松了口气）：“终于……拿回来了。”\n声音：水声、背带滴水',
     "机位 / 运镜 / 连续性": "固定机位，不切", "叙事目的": "装备回到手里", "预算秒": 12},
    {"镜号": "A2", "摄影角度": "平视", "景别": "近景", "场景": "溪岸",
     "画面内容 / 动作": "灰色山羊冲来，梁舟举棍横挡。",
     "台词 / 声音": "声音：蹄声由远及近，撞击后蹄声停止",
     "机位 / 运镜 / 连续性": "同机位续接", "叙事目的": "冲突", "预算秒": 8},
]

BINDING = {"video_title": "溪岸", "hook": "背包还在水中", "summary": "取包，挡住山羊",
           "skipped_segments": [],
           "speaker_names": [{"written": "梁舟", "name": "梁舟"}],
           "location_names": [{"written": "溪岸", "name": "溪岸"}],
           "bindings": [
               {"镜号": "A1", "segment_id": "seg_1", "source_quote": SEGMENTS[0]["text"],
                "start_state": "背包还在水里", "end_state": "梁舟坐在岸边，背包在膝前",
                "light": "白天，斜射日光", "in_frame": ["梁舟"], "extras": [], "actions": []},
               {"镜号": "A2", "segment_id": "seg_2", "source_quote": SEGMENTS[1]["text"],
                "start_state": "梁舟坐在岸边", "end_state": "木棍挡住山羊",
                "light": "白天，斜射日光", "in_frame": ["梁舟"],
                "extras": ["灰色野山羊"], "actions": []}]}


@pytest.fixture
def traced(tmp_path, monkeypatch):
    """The sheet, taken all the way to the request H3 would receive."""
    merged = merge({"shots": SHEETS}, BINDING)
    ctx = PlannerContext.from_env()
    configure_budget(100, fast=False, ctx=ctx)
    ctx.spoken_range = (0, 300)
    ctx.authored_storyboard = True
    bible = StoryBible.model_validate(BIBLE)
    valid = validate_and_normalize(merged, SEGMENTS, bible, {"溪岸": bible.locations[0]},
                                   "\n".join(s["text"] for s in SEGMENTS), ctx=ctx)
    assert not valid.errors, valid.errors

    book = tmp_path / "trial"
    episode = book / "trial_1"
    episode.mkdir(parents=True)
    (book / "story_bible.json").write_text(json.dumps(BIBLE, ensure_ascii=False), encoding="utf-8")
    (book / "profile.json").write_text(json.dumps({"style": "3d", "frame": "16:9"}), encoding="utf-8")
    (episode / "segments.json").write_text(json.dumps(SEGMENTS, ensure_ascii=False), encoding="utf-8")
    script = {"profile": {"style": "3d", "frame": "16:9"}, "shots": valid.shots}
    (episode / "chapter_script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    plan, _ = compile_plan(script, load_context(episode, book / "story_bible.json"))

    def translate(parts, schema, **kwargs):
        lines = [line for line in parts[0]["text"].splitlines() if line[:1].isdigit()]
        if schema.get("properties", {}).get("manners"):
            return {"manners": ["The line is delivered in a relieved, breathy voice."] * len(lines)}
        return {"shots": [f"A young man on a stream bank, shot {i}." for i in range(1, len(lines) + 1)]}

    monkeypatch.setattr(h3, "ask_json", translate)
    for clip in plan["clips"]:
        assert h3.convert(clip, tries=1), clip["clip_id"]
    return SHEETS, valid.shots, plan


# --- 原表 ↔ chapter_script ------------------------------------------------------------------------

def test_the_shot_order_and_count_are_the_sheets(traced):
    sheet, shots, _ = traced
    assert [s["authored_id"] for s in shots] == [row["镜号"] for row in sheet]


def test_the_authored_picture_columns_arrive_verbatim(traced):
    sheet, shots, _ = traced
    assert [s["motion_prompt"] for s in shots] == [row["画面内容 / 动作"] for row in sheet]
    assert [s["shot_scale"] for s in shots] == [row["景别"] for row in sheet]
    for shot, row in zip(shots, sheet):
        assert row["摄影角度"] in shot["camera"] and row["机位 / 运镜 / 连续性"] in shot["camera"]


def test_the_dialogue_keeps_its_words_its_owner_and_how_it_is_spoken(traced):
    sheet, shots, _ = traced
    written = authored_sound(sheet[0]["台词 / 声音"]).turns[0]
    turn = shots[0]["turns"][0]
    assert (turn["text"], turn["speaker_name"]) == (written["text"], "梁舟")
    assert turn["emotion"] == "松了口气"
    # a shot whose cell holds only 声音 gets no speech invented for it
    assert [t for t in shots[1]["turns"] if t["delivery_mode"] != "silent_action"] == []


def test_the_authored_sound_is_the_sheets_sound(traced):
    sheet, shots, _ = traced
    assert shots[1]["sfx"] == authored_sound(sheet[1]["台词 / 声音"]).sfx
    assert "蹄声停止" in shots[1]["sfx"]


def test_the_planned_length_is_the_authors_number(traced):
    sheet, shots, _ = traced
    assert [s["duration_seconds"] for s in shots] == [float(row["预算秒"]) for row in sheet]


# --- chapter_script ↔ clip_plan --------------------------------------------------------------------

def test_the_plan_is_marked_as_a_cut_somebody_made(traced):
    """Without this the packer treats an authored sheet as a model's stage list: the per-shot
    durations never become shot_timing and the medium sentence is never written."""
    _, _, plan = traced
    video = [c for c in plan["clips"] if c["kind"] == "video"]
    assert video and all(c.get("scene_ids") for c in video)


def test_every_planned_second_reaches_the_plan(traced):
    sheet, _, plan = traced
    timing = [t["seconds"] for c in plan["clips"] if c["kind"] == "video" for t in c["shot_timing"]]
    assert timing == [float(row["预算秒"]) for row in sheet]


def test_the_books_medium_reaches_the_plan(traced):
    _, _, plan = traced
    assert {c["render_family"] for c in plan["clips"] if c["kind"] == "video"} == {"3d"}


def test_the_sound_reaches_the_plan_shot_by_shot(traced):
    sheet, _, plan = traced
    sounds = [s for c in plan["clips"] if c["kind"] == "video" for s in c["shot_sound"]]
    assert any("蹄声停止" in s for s in sounds)


# --- clip_plan ↔ the request that is sent -----------------------------------------------------------

def test_the_request_says_what_this_book_is_rendered_as(traced):
    _, _, plan = traced
    for clip in (c for c in plan["clips"] if c["kind"] == "video"):
        assert "consistent stylized 3D animation" in clip["prompt_h3"]


def test_the_request_carries_the_authored_duration_of_each_shot(traced):
    sheet, _, plan = traced
    text = "\n".join(c["prompt_h3"] for c in plan["clips"] if c["kind"] == "video")
    for row in sheet:
        assert f"Planned duration: {float(row['预算秒']):g} seconds." in text


def test_the_line_is_spoken_by_its_owner_in_the_authors_words_and_manner(traced):
    sheet, _, plan = traced
    spoken = next(c for c in plan["clips"] if c.get("dialogue_bindings"))
    words = authored_sound(sheet[0]["台词 / 声音"]).turns[0]["text"]
    assert f"<d>[Chinese] {words.replace('。', '.')}</d>" in spoken["prompt_h3"]
    assert "relieved, breathy voice" in spoken["prompt_h3"]


def test_no_request_contradicts_itself(traced):
    _, _, plan = traced
    for clip in (c for c in plan["clips"] if c["kind"] == "video"):
        assert not request_issues(clip), clip["clip_id"]
