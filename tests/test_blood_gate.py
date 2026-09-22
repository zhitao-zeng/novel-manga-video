"""A gate written for one platform's refusal, kept after the platform changed.

FORBIDDEN_VISUAL's blood rule was written in the first book's week, against the paid platform's
moderation, with that book's own fix in the error text ("碑上的结果改写为无字的发光纹路").  Then the
local H3 arrived, which refuses nothing - sent a knife coming down and blood spreading into standing
water it drew exactly that - and the rule went on gating plans for it.  On 在美漫当心灵导师的日子 the
gate sent nine authored chapters' shots to the patch round, which asked the model for a rewrite: ch1
shot 12 went in as a dog fight and came back as a corridor in a mind palace, with its shot number
gone.

Two facts, each a separate rule.  A service that does not refuse blood is not warned about blood.
And a sheet an author cut is never rewritten by the pipeline, whatever the service - reported, and
left to the render stage, which already handles a refusal.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from novel_manga.planning.context import PlannerContext
from novel_manga.planning.normalization import end_state_and_visual_checks

BLOOD = {"visual_prompt": "席勒上前一步夺回厨刀手起刀落；犬四肢摊平，暗色的血漫进积水", "motion_prompt": "夺刀",
         "end_state": "血迹漫开", "camera": "微仰中景缓慢推近，落幅停在血迹", "light": "冷顶光"}
TEXT = {"visual_prompt": "碑上写着“天命”二字", "motion_prompt": "抬头", "end_state": "看碑",
        "camera": "固定", "light": "月光"}


def gate(shot, **ctx_fields):
    ctx = PlannerContext(**ctx_fields)
    errors, warnings = [], []
    end_state_and_visual_checks(shot, [], "clip_1 stage 1", ctx, errors, warnings)
    return errors, warnings


def test_a_service_that_moderates_still_gates_blood_in_the_ordinary_planner():
    errors, warnings = gate(BLOOD, renderer_moderates=True)
    assert [e.field for e in errors] and all("血液或伤口" in str(e) for e in errors)


def test_the_local_h3_is_not_warned_about_what_it_will_draw_as_written():
    errors, warnings = gate(BLOOD, renderer_moderates=False)
    assert errors == [] and not any("血液" in w for w in warnings)


def test_an_authored_sheet_is_reported_and_never_sent_for_a_rewrite_whatever_the_service():
    errors, warnings = gate(BLOOD, renderer_moderates=True, authored_storyboard=True)
    assert errors == []
    assert any(w.startswith("report only:") and "血液或伤口" in w for w in warnings)


def test_readable_text_is_still_a_gate_where_it_was_one_and_a_note_on_an_authored_sheet():
    errors, _ = gate(TEXT, renderer_moderates=False)
    assert errors and "可读文字" in str(errors[0])
    errors, warnings = gate(TEXT, renderer_moderates=False, authored_storyboard=True)
    assert errors == [] and any("可读文字" in w for w in warnings)


def test_the_fix_the_model_is_given_belongs_to_no_particular_book():
    errors, _ = gate(BLOOD, renderer_moderates=True)
    assert "碑" not in str(errors[0]) and "灵" not in str(errors[0])
    assert "姿态、表情" in str(errors[0])


@pytest.mark.parametrize("model, moderates", [("minimax-h3-ref2va-turbo", False), ("sd2.5", True), ("", True)])
def test_the_context_learns_from_the_video_model_whether_the_service_moderates(monkeypatch, model, moderates):
    monkeypatch.setenv("NOVEL_VIDEO_MODEL", model)
    assert PlannerContext.from_env().renderer_moderates is moderates


# ---- the trace, on split lines ----------------------------------------------------------------------

def load_trace():
    path = Path(__file__).resolve().parents[1] / "scripts" / "authored_trace_thin.py"
    spec = importlib.util.spec_from_file_location("authored_trace_thin", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def turn(text, speaker="席勒", emotion="平静", mode="visible_dialogue"):
    return {"text": text, "speaker_name": speaker, "written_speaker": speaker, "emotion": emotion, "delivery_mode": mode}


def test_a_line_the_pipeline_split_in_two_is_still_the_authors_line():
    """ch8 shot 5: one written line, two script turns, every character kept."""
    trace = load_trace()
    want = [turn("几百亿不做慈善，穿一身可笑的紧身衣去小巷跟小混混打架，图什么？"), turn("因为你的根本目的不是救助。是报复。")]
    got = [turn("几百亿不做慈善，穿一身可笑的紧身衣去小巷跟小混混打架，"), turn("图什么？"), turn("因为你的根本目的不是救助。是报复。")]
    matched, extra = trace.pair_turns(want, got)
    assert extra == []
    assert [joined for _, _, joined in matched] == [w["text"] for w in want]
    assert [g["text"] for _, g, _ in matched] == [got[0]["text"], got[2]["text"]]   # the first piece answers


def test_a_line_that_was_really_changed_is_still_caught():
    trace = load_trace()
    matched, _ = trace.pair_turns([turn("你到底为什么要这么做？值得吗？")], [turn("你为什么要这么做？")])
    (w, g, joined), = matched
    assert joined != w["text"]


def test_a_line_the_script_added_is_left_over_and_a_line_it_dropped_pairs_with_nothing():
    trace = load_trace()
    matched, extra = trace.pair_turns([turn("甲"), turn("乙")], [turn("甲"), turn("乙"), turn("丙")])
    assert [t["text"] for t in extra] == ["丙"]
    matched, extra = trace.pair_turns([turn("甲"), turn("乙")], [turn("甲")])
    assert matched[1][1] is None and extra == []


# ---- a patch on an authored stage ----------------------------------------------------------------

from novel_manga.planning.binding import AUTHORED_FIELDS, keep_authored


def authored_stage(shot_id, **bound):
    return {"authored_id": shot_id, "shot_id": shot_id, "event": f"镜{shot_id}的画面", "shot_scale": "中景",
            "camera": "微仰；缓慢推近", "duration_seconds": 7.0, "purpose": "立住人物", "sfx": "刀落闷响",
            "turns": [{"speaker_name": "席勒", "text": "别动。", "delivery_mode": "visible_dialogue", "emotion": "", "chat_target": ""}],
            "authored_seconds": 7.0, "authored_angle": "微仰", "scene_id": "scene_01",
            "segment_id": "seg_3", "source_quote": "席勒说:“心理学，别选我的课，小子。”", "start_state": "起", "end_state": "落",
            "light": "冷顶光", "in_frame": ["席勒"], "extras": [], "actions": [], **bound}


def test_a_patch_may_correct_the_quote_and_nothing_the_author_wrote():
    """ch1, second time round: the quote had a 席勒说: in front of it, the patch fixed that, and
    the whole stage came back as a different scene with no shot number."""
    before = {"clips": [{"clip_id": "clip_4", "stages": [authored_stage("11"), authored_stage("12")]}]}
    rewritten = {**authored_stage("12", source_quote="“心理学，别选我的课，小子。”", end_state="席勒靠回椅背"),
                 "event": "布鲁斯询问系别，席勒靠回椅背，表情敷衍。", "shot_scale": "特写", "duration_seconds": 3.0,
                 "turns": [], "purpose": ""}
    for key in ("authored_id", "authored_seconds", "authored_angle", "shot_id"):
        rewritten.pop(key)
    after = {"clips": [{"clip_id": "clip_4", "stages": [authored_stage("11"), rewritten]}]}
    kept = keep_authored(before, after)
    stage = kept["clips"][0]["stages"][1]
    assert stage["source_quote"] == "“心理学，别选我的课，小子。”"      # the fix is kept
    assert stage["end_state"] == "席勒靠回椅背"                        # the model's own column, kept
    assert stage["authored_id"] == "12" and stage["event"] == "镜12的画面"
    assert stage["shot_scale"] == "中景" and stage["duration_seconds"] == 7.0 and stage["purpose"] == "立住人物"
    assert stage["turns"][0]["text"] == "别动。"
    assert all(stage[k] == before["clips"][0]["stages"][1][k] for k in AUTHORED_FIELDS)


def test_a_stage_the_patch_did_not_touch_is_not_touched_either():
    before = {"clips": [{"clip_id": "c", "stages": [authored_stage("1")]}]}
    assert keep_authored(before, before) == before


def test_a_patch_that_changed_the_shape_of_an_authored_cut_is_refused():
    before = {"clips": [{"clip_id": "c", "stages": [authored_stage("1")]}]}
    after = {"clips": [{"clip_id": "c", "stages": [authored_stage("1"), authored_stage("x")]}]}
    with pytest.raises(ValueError, match="number of stages"):
        keep_authored(before, after)


def test_a_stage_that_was_never_authored_is_left_to_the_patch():
    """The ordinary planner's stages carry no authored_id and the patch owns them entirely."""
    plain = {k: v for k, v in authored_stage("1").items() if k not in ("authored_id", "authored_seconds", "authored_angle")}
    before = {"clips": [{"clip_id": "c", "stages": [plain]}]}
    after = {"clips": [{"clip_id": "c", "stages": [{**plain, "event": "改了"}]}]}
    assert keep_authored(before, after)["clips"][0]["stages"][0]["event"] == "改了"


def test_the_patch_prompt_carries_no_stele_and_no_blood_rule_for_a_service_that_draws_it(monkeypatch):
    from novel_manga.application.planning import requests as planner_requests
    seen = {}
    def fake_ask(parts, schema, **kwargs):
        seen["prompt"] = "".join(p.get("text", "") for p in parts)
        return {"insertions": [], "replacements": []}
    monkeypatch.setattr("novel_manga.llm.client.ask_json", fake_ask)
    raw = {"clips": [{"clip_id": "clip_1", "stages": [authored_stage("1")]}]}
    segments = [{"segment_id": "seg_3", "text": "原文"}]
    for moderates in (True, False):
        planner_requests.patch_plan(raw, [], {"clip_1 stage 1": ["x"]}, segments, ["席勒"], ["教室"],
                                    ctx=PlannerContext(renderer_moderates=moderates, authored_storyboard=True))
        assert "碑" not in seen["prompt"]
        assert ("不得出现血液" in seen["prompt"]) is moderates
