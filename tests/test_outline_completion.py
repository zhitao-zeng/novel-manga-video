from __future__ import annotations

import json

import pytest

import novel_manga.planning.constants as pc_constants
import novel_manga.planning.contracts as pc_contracts
import plan_chapter_thin as plan_chapter
import planner_requests_thin as planner_requests
import sys
from novel_manga.planning.context import PlannerContext


def complete(mode="coverage"):
    return json.dumps({"sections": {name: "明确的原文事件与拍摄安排" for name in pc_constants.OUTLINE_SECTIONS[mode]},
                       "coverage": [{"segment_id": "seg_1", "placement": "第一片段"}]}, ensure_ascii=False)


def kwargs():
    return dict(base_url="http://model.invalid/v1", model="same-model", payload={"segments": [{"segment_id": "seg_1", "text": "原文"}]},
                schema={"type": "object"}, max_tokens=12000, timeout=5, fast=True)


def test_truncated_reasoning_is_never_forwarded_as_an_outline(monkeypatch):
    planner_ctx = PlannerContext.from_env()
    requests = []

    def response(client, urls, headers, body):
        requests.append(body)
        return {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning": "unfinished" * 2000}}], "usage": {}}

    monkeypatch.setattr(planner_requests, "_post_any", response)
    with pytest.raises(planner_requests.IncompleteOutlineError, match="empty content"):
        planner_requests.call_model(**kwargs(), ctx=planner_ctx)
    assert [r["max_tokens"] for r in requests] == [4096, 8192]
    assert all(r["response_format"]["json_schema"]["name"] == "chapter_outline" for r in requests)
    assert all("unfinished" not in json.dumps(r) for r in requests)


def test_even_parseable_length_output_must_complete_before_pass_two(monkeypatch):
    planner_ctx = PlannerContext.from_env()
    requests = []

    def response(client, urls, headers, body):
        requests.append(body)
        return {"choices": [{"finish_reason": "length" if len(requests) == 1 else "stop",
                             "message": {"content": complete() if len(requests) <= 2 else '{"clips": []}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}

    monkeypatch.setattr(planner_requests, "_post_any", response)
    _, meta = planner_requests.call_model(**kwargs(), ctx=planner_ctx)
    assert [r["response_format"]["json_schema"]["name"] for r in requests] == ["chapter_outline", "chapter_outline", "thin_chapter_clips"]
    assert meta["outline_complete"] is True
    assert meta["analysis_usage"]["total_tokens"] == 60
    assert meta["outline_content_chars"] == meta["outline_forwarded_chars"] == len(complete())


def test_missing_story_section_or_duplicate_coverage_is_incomplete():
    data = json.loads(complete("story"))
    del data["sections"]["causal_chain"]
    assert any("causal_chain" in e for e in pc_contracts.validate_outline(json.dumps(data), "story", ["seg_1"]))
    data = json.loads(complete())
    data["coverage"] *= 2
    assert pc_contracts.validate_outline(json.dumps(data), "coverage", ["seg_1", "seg_2"])
    data["coverage"][0]["placement"] = " "
    assert any("placement" in e for e in pc_contracts.validate_outline(json.dumps(data), "coverage", ["seg_1", "seg_2"]))


def test_cli_reports_incomplete_outline_without_outer_retry(monkeypatch, tmp_path):
    planner_ctx = PlannerContext.from_env()
    source = tmp_path / "source.txt"
    source.write_text("第一章 庭院\n" + "主角走入庭院，看见院门紧闭。\n" * 30)
    bible = tmp_path / "bible.json"
    bible.write_text(json.dumps({"novel_title": "测试", "genre": "通用", "visual_style": "国漫", "palette": "青色", "style_fingerprint": "test",
                                 "characters": [{"name": "主角", "role": "主角", "appearance": "黑发", "wardrobe": "青衣"}], "locations": ["庭院：空旷院落"]}))
    calls = []

    def failure(**kw):
        calls.append(kw)
        raise planner_requests.IncompleteOutlineError([{"finish_reason": "length", "errors": ["empty content"]}])

    monkeypatch.setattr(planner_requests, "call_model", failure)
    monkeypatch.setattr('identity_flow_thin.resolve_chapter', lambda *a, **k: {})
    monkeypatch.setattr(sys, "argv", ["plan_chapter_thin.py", str(source), "--novel-id", "demo", "--bible", str(bible),
                                               "--output-root", str(tmp_path / "out"), "--max-redo", "3"])
    assert plan_chapter.main(context=planner_ctx) == 2
    assert len(calls) == 1
    report = json.loads((tmp_path / "out/demo/demo_1/planning_failed.json").read_text())
    assert report["attempts"][0]["stage"] == "outline"
    assert report["attempts"][0]["outline_complete"] is False
    assert not (tmp_path / "out/demo/demo_1/chapter_script.json").exists()
