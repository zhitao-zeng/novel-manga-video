from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from scripts import build_clip_plan_thin as packer

# The standalone scripts import their siblings from the scripts directory.
import novel_manga.planning.validation as pc_validation
import plan_chapter_thin as plan_chapter
import planner_requests_thin as planner_requests
import sys
import time
from novel_manga.planning.context import PlannerContext
from novel_manga import model_client


def _shot(index: int, clip_hint: str = "clip_1") -> dict:
    return {
        "index": index, "clip_hint": clip_hint, "location": "庭院",
        "segment_id": "seg_01", "shot_scale": "中景",
        "motion_prompt": "人物转身", "visual_prompt": "人物站在庭院",
        "end_state": "人物面向院门",
        "turns": [{"delivery_mode": "silent_action", "speaker_name": "", "text": "转身"}],
    }


def test_short_clip_merge_preserves_shots_without_overflowing_stage_labels():
    shots = [_shot(i) for i in range(1, 8)]  # 6 stages + a 4-second tail
    clips = packer.pack(shots)
    assert [len(c["shots"]) for c in clips] == [6, 1]
    assert [s["index"] for c in clips for s in c["shots"]] == list(range(1, 8))
    for clip in clips:
        clip["request_seconds"] = int(clip["seconds"])
        prompt = packer.compile_prompt(clip, SimpleNamespace(visual_style="国漫"), [], [], "庭院")
        assert prompt.count("【阶段") == len(clip["shots"])


def test_short_clip_still_merges_when_six_stages_fit():
    shots = [_shot(i) for i in range(1, 6)] + [_shot(6, "clip_2")]
    clips = packer.pack(shots)
    assert len(clips) == 1
    assert [s["index"] for s in clips[0]["shots"]] == list(range(1, 7))


@pytest.mark.parametrize('description', [
    '三头龙共用一个身体，分别向三个方向咆哮',
    '三头雪铠共用一个身体，分别观察洞口',
    '三个独立个体在庭院分别站立，互相交谈',
])
def test_packer_does_not_guess_anatomy_from_species_words(description):
    shot = {**_shot(1), 'visual_prompt': description}
    clip = packer.pack([shot])[0]
    clip['request_seconds'] = int(clip['seconds'])
    prompt = packer.compile_prompt(clip, SimpleNamespace(visual_style='国漫'), [], [], '庭院')
    assert description in prompt


def _mock_model(monkeypatch, handler):
    client_type = httpx.Client
    monkeypatch.setattr(model_client.httpx, "Client", lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(model_client, "endpoint_order", lambda _: ["http://model.invalid/v1"])


def _response(content: str, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]})


@pytest.mark.parametrize(("initial", "expected"), [(3500, [3500, 7000]), (6000, [6000, 8000])])
def test_truncated_json_gets_only_one_retry_with_capped_tokens(monkeypatch, initial, expected):
    tokens = []

    def model(request):
        tokens.append(json.loads(request.content)["max_tokens"])
        return _response('{"unfinished":', "length")

    _mock_model(monkeypatch, model)
    with pytest.raises(ValueError, match="JSON truncated"):
        model_client.ask_json([], {}, name="names", max_tokens=initial)
    assert tokens == expected


@pytest.mark.parametrize("first_failure", ["truncation", "unavailable"])
def test_model_retries_share_remaining_timeout(monkeypatch, first_failure):
    clock = [0.0]
    timeouts = []
    monkeypatch.setattr(model_client.time, "monotonic", lambda: clock[0])

    def model(request):
        timeouts.append(request.extensions["timeout"]["read"])
        if len(timeouts) == 1:
            clock[0] += 6
            return _response('{"unfinished":', "length") if first_failure == "truncation" else httpx.Response(503)
        return _response('{"ok": true}')

    _mock_model(monkeypatch, model)
    monkeypatch.setattr(model_client, "endpoint_order", lambda _: ["http://one.invalid/v1", "http://two.invalid/v1"])
    assert model_client.ask_json([], {}, name="names", timeout=10) == {"ok": True}
    assert timeouts == [10, 4]


def test_no_model_retry_after_time_budget_is_spent(monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(model_client.time, "monotonic", lambda: clock[0])

    def model(request):
        calls.append(request)
        clock[0] += 10
        return _response('{"unfinished":', "length")

    _mock_model(monkeypatch, model)
    with pytest.raises(TimeoutError, match="request budget exhausted"):
        model_client.ask_json([], {}, name="names", timeout=10)
    assert len(calls) == 1


def test_plan_patch_does_not_retry_truncated_json(monkeypatch):
    planner_ctx = PlannerContext.from_env()
    requests = []

    def model(request):
        requests.append(request)
        return _response('{"replacements":', "length")

    _mock_model(monkeypatch, model)
    raw = {"clips": [{"clip_id": "clip_1", "stages": [{"segment_id": "seg_01"}]}]}
    with pytest.raises(ValueError, match="JSON truncated"):
        planner_requests.patch_plan(raw, [], {"clip_1 stage 1": ["missing speaker"]},
                           [{"segment_id": "seg_01", "text": "原文中的一句对白。"}], ["主角"], ["庭院"], timeout=9, ctx=planner_ctx)
    assert len(requests) == 1
    assert 0 < requests[0].extensions["timeout"]["read"] <= 9


def test_chapter_repair_budget_survives_full_draft_retries(monkeypatch, tmp_path):
    planner_ctx = PlannerContext.from_env()
    source = tmp_path / "novel.txt"
    source.write_text("第一章 庭院\n" + "主角走入庭院，看见院门紧闭。\n" * 40, encoding="utf-8")
    bible = tmp_path / "bible.json"
    bible.write_text(json.dumps({
        "novel_title": "测试", "genre": "通用", "visual_style": "国漫", "palette": "青色", "style_fingerprint": "test-style",
        "characters": [{"name": "主角", "role": "主角", "appearance": "黑发", "wardrobe": "青衣"}],
        "locations": ["庭院：空旷的院落"],
    }), encoding="utf-8")
    clock = [0.0]
    patch_timeouts = []
    drafts = []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def draft(**kwargs):
        drafts.append(kwargs)
        return '{"clips": []}', {}

    def failed_patch(*args, timeout, **kwargs):
        patch_timeouts.append(timeout)
        clock[0] += timeout
        raise TimeoutError("simulated slow repair")

    monkeypatch.setattr(planner_requests, "call_model", draft)
    monkeypatch.setattr('identity_flow_thin.resolve_chapter', lambda *a, **k: {})
    monkeypatch.setattr(pc_validation, "validate_and_normalize", lambda *args, **kwargs: (["clip_1 stage 1: missing speaker"], [], []))
    monkeypatch.setattr(planner_requests, "patch_plan", failed_patch)
    monkeypatch.setattr(sys, "argv", ["plan_chapter_thin.py", str(source), "--novel-id", "demo",
                        "--bible", str(bible), "--output-root", str(tmp_path / "out"), "--max-redo", "2"])
    assert plan_chapter.main(context=planner_ctx) == 2
    assert len(drafts) == 3
    assert patch_timeouts == [120, 60]  # the third draft gets no fresh repair allowance
    report = json.loads((tmp_path / "out/demo/demo_1/planning_failed.json").read_text())
    assert [len(a.get("patches", [])) for a in report["attempts"]] == [1, 1, 0]
    assert report["elapsed_seconds"] == 180
