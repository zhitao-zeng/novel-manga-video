from __future__ import annotations

import copy
import json

import pytest

import plan_chapter_thin as planner
from novel_manga.models import Character, StoryBible


@pytest.fixture(autouse=True)
def restore_budget(monkeypatch):
    for name in (
        "CLIP_SECONDS_MAX", "SHORT_CLIPS", "MAX_CLIP_SECONDS", "CLIP_RANGE", "STAGE_RANGE", "SPOKEN_RANGE",
        "EPISODE_SECONDS_TARGET", "EPISODE_SECONDS_MIN", "EPISODE_SECONDS_MAX",
        "SYSTEM_PROMPT", "FAST_TIER", "ANONYMOUS_SPEAKERS", "TEXT_ON_PROPS_GATE",
    ):
        monkeypatch.setattr(planner, name, getattr(planner, name))


@pytest.mark.parametrize("cap", [15, 30])
@pytest.mark.parametrize("fast,chars,target", [(False, 3000, 90), (True, 2500, 75), (True, 6000, 150)])
def test_every_prompt_budget_matches_lane_and_episode(monkeypatch, cap, fast, chars, target):
    monkeypatch.setattr(planner, "CLIP_SECONDS_MAX", float(cap))
    monkeypatch.setattr(planner, "SHORT_CLIPS", cap == 15)
    budget = planner.configure_budget(chars, fast=fast)
    requirements = planner.budget_requirements()
    brief = planner.render_brief(planner.SYSTEM_PROMPT)

    assert budget["episode_target_seconds"] == target
    assert budget["clip_count"][1] * cap >= target
    assert target <= budget["episode_max_seconds"] <= budget["clip_count"][1] * cap
    assert planner.MAX_CLIP_SECONDS == cap
    assert requirements["clip_seconds"] == f"{10 if cap == 15 else 20}-{cap}"
    assert f"约{target}秒" in brief
    assert f"规划上限{budget['episode_max_seconds']:g}秒" in brief
    assert f"{budget['spoken_chars'][0]}到{budget['spoken_chars'][1]}字" in brief
    assert "全集不得超过100秒" not in brief
    assert "{episode_" not in brief
    if target == 150 and cap == 15:
        assert budget["clip_count"][1] == 10  # eight clips cannot carry this episode


def test_floor_is_not_promised_beyond_schema_capacity(monkeypatch):
    monkeypatch.setattr(planner, "CLIP_SECONDS_MAX", 15.0)
    monkeypatch.setattr(planner, "SHORT_CLIPS", True)
    with pytest.raises(ValueError, match="规划容量"):
        planner.configure_budget(3000, fast=True, min_seconds=160)


def test_subsecond_floor_shortfall_does_not_trigger_a_full_rewrite(monkeypatch):
    text = "主角站在庭院门口说：“门已经打开请跟我来。”"
    bible = StoryBible(novel_title="测试", genre="通用", visual_style="国漫", palette="青色", style_fingerprint="test",
                       characters=[Character(name="主角", role="主角", appearance="黑发", wardrobe="青衣")], locations=["庭院：空旷的院落"])
    stage = {"segment_id": "seg_1", "source_quote": "主角站在庭院门口", "start_state": "主角站在门口", "event": "主角抬手示意",
             "end_state": "主角抬手", "camera": "门外平视", "light": "日光从左侧照入", "sfx": "", "shot_scale": "中景",
             "in_frame": ["主角"], "actions": [], "extras": [],
             "turns": [{"speaker_name": "主角", "delivery_mode": "visible_dialogue", "text": "门已经打开请跟我来。", "emotion": "平静", "chat_target": ""}]}
    raw = {"clips": [{"clip_id": "clip_1", "location": "庭院", "characters": ["主角"], "stages": [stage], "avoid": ""}], "skipped_segments": []}
    args = (raw, [{"segment_id": "seg_1", "text": text}], bible, {"庭院": bible.locations[0]}, text)
    seconds = planner.stage_seconds(stage["turns"])
    monkeypatch.setattr(planner, "EPISODE_SECONDS_MIN", seconds + 0.25)
    errors, warnings, _ = planner.validate_and_normalize(*args)
    assert not errors
    assert any("估时容差内，不重写" in warning for warning in warnings)
    monkeypatch.setattr(planner, "EPISODE_SECONDS_MIN", seconds + 2.25)
    errors, _, _ = planner.validate_and_normalize(*args)
    assert any("低于本次要求的下限" in error for error in errors)


def test_ab_has_same_limits_and_each_complete_outline_reaches_pass_two(monkeypatch):
    monkeypatch.setattr(planner, "CLIP_SECONDS_MAX", 15.0)
    monkeypatch.setattr(planner, "SHORT_CLIPS", True)
    planner.configure_budget(4000, fast=True)
    requests = []
    outlines = []

    def response(client, urls, headers, body):
        requests.append(copy.deepcopy(body))
        schema = body["response_format"]["json_schema"]
        if schema["name"] == "chapter_outline":
            sections = schema["schema"]["properties"]["sections"]["required"]
            content = json.dumps({"sections": {name: "原文因果与片段安排" * 500 for name in sections},
                                  "coverage": [{"segment_id": "seg_1", "placement": "片段一"}]}, ensure_ascii=False)
            outlines.append(content)
        else:
            content = '{"clips": []}'
        return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}], "usage": {}}

    monkeypatch.setattr(planner, "_post_any", response)
    monkeypatch.setattr(planner, "_post", response)
    kwargs = dict(base_url="http://model.invalid/v1", model="same-model", payload={"requirements": planner.budget_requirements(), "segments": [{"segment_id": "seg_1", "text": "原文"}]},
                  schema={"type": "object"}, max_tokens=12000, timeout=5, fast=True, seed=37)
    planner.call_model(**kwargs)  # production default remains coverage
    planner.call_model(**kwargs, outline_mode="story")
    a_first, a_final, b_first, b_final = requests
    assert a_final["messages"][:2] == b_final["messages"][:2]
    assert a_final["messages"][-1]["content"].split("完整提纲：", 1)[1] == outlines[0]
    assert b_final["messages"][-1]["content"].split("完整提纲：", 1)[1] == outlines[1]
    assert len(outlines[0]) > 6000  # no 3,000/6,000-character suffix truncation
    assert a_first["messages"][1:] == b_first["messages"][1:]
    assert "causal_chain" not in a_first["messages"][0]["content"]
    assert "关键事实及因果链" in b_first["messages"][0]["content"]
    assert {k: v for k, v in a_first.items() if k not in ("messages", "response_format")} == {k: v for k, v in b_first.items() if k not in ("messages", "response_format")}
    assert all(r["seed"] == 37 for r in requests)
    assert a_first["max_tokens"] == b_first["max_tokens"] == 4096
    assert a_first["chat_template_kwargs"] == {"enable_thinking": False}
    assert "或明确跳过" not in a_first["messages"][0]["content"]


def test_cli_dry_run_carries_the_actual_long_episode_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(planner, "CLIP_SECONDS_MAX", 15.0)
    monkeypatch.setattr(planner, "SHORT_CLIPS", True)
    source = tmp_path / "novel.txt"
    source.write_text("第一章 庭院\n" + "主角走入庭院，看见院门紧闭。\n" * 500)
    bible = tmp_path / "bible.json"
    bible.write_text(json.dumps({
        "novel_title": "测试", "genre": "通用", "visual_style": "国漫", "palette": "青色", "style_fingerprint": "test-style",
        "characters": [{"name": "主角", "role": "主角", "appearance": "黑发", "wardrobe": "青衣"}],
        "locations": ["庭院：空旷的院落"],
    }))
    monkeypatch.setattr(planner.sys, "argv", ["plan_chapter_thin.py", str(source), "--novel-id", "demo", "--bible", str(bible),
                                              "--output-root", str(tmp_path / "out"), "--tier", "fast", "--dry-run"])
    assert planner.main() == 0
    request = json.loads((tmp_path / "out/demo/demo_1/request_dry_run.json").read_text())
    assert request["planning_budget"]["episode_target_seconds"] == 150
    assert request["requirements"]["clip_count"] == "6-10"
    assert request["requirements"]["clip_seconds"] == "10-15"
    assert request["requirements"]["episode_seconds"] == "about 150, max 150"
    assert request["requirements"]["spoken_chars_total"] == "240-400"
