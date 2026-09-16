from __future__ import annotations

import pytest

from benchmark_planner_outline import audit_outline_trace, write
from judge_planner_outline import judge_one, shot_view
import novel_manga.planning.constants as pc_constants
import novel_manga.planning.contracts as pc_contracts


def test_blind_review_cannot_count_summary_or_source_quotes_as_filmed_information():
    script = {
        "summary": "人物已识破骗局。",
        "hook": "骗局将被揭穿。",
        "shots": [{"source_quote": "他早就知道那是一场骗局。", "visual_prompt": "人物皱眉。",
                   "motion_prompt": "人物把杯子放下。", "turns": [{"delivery_mode": "silent_action", "text": "放下杯子"}]}],
    }
    view = shot_view(script)
    assert "骗局" not in str(view)
    assert view[0]["motion_prompt"] == "人物把杯子放下。"
    assert view[0]["turns"] == script["shots"][0]["turns"]


def test_changed_planning_context_is_not_sent_for_a_blind_comparison(tmp_path):
    for arm, actor in (("coverage", "主角"), ("story", "另一人")):
        run = tmp_path / "runs/demo_1/repeat_01" / arm
        episode = run / "output/demo/demo_1"
        write(run / "result.json", {"status": "passed", "episode_dir": str(episode)})
        write(episode / "request_attempt_01.json", {"available_characters": [actor]})
    with pytest.raises(ValueError, match="contexts differ"):
        judge_one(tmp_path, {}, {"id": "demo_1", "repeat": 1}, 0)


def test_real_trace_rejects_cropped_outline_even_when_both_responses_stop(tmp_path):
    import json
    outline = json.dumps({"sections": {key: "concrete source event and allocation " * 100 for key in pc_constants.OUTLINE_SECTIONS["coverage"]},
                          "coverage": [{"segment_id": "seg_1", "placement": "clip one"}]})
    first = {"response_format": {"json_schema": {"name": "chapter_outline"}},
             "messages": [{"role": "system", "content": "outline"}, {"role": "user", "content": json.dumps({"segments": [{"segment_id": "seg_1"}]})}]}
    second = {"response_format": {"json_schema": {"name": "thin_chapter_clips"}},
              "messages": [{"role": "user", "content": "完整提纲：" + outline[-3000:]}]}
    write(tmp_path / "http/001-request.json", first)
    write(tmp_path / "http/001-response.json", {"choices": [{"finish_reason": "stop", "message": {"content": outline}}]})
    write(tmp_path / "http/002-request.json", second)
    write(tmp_path / "http/002-response.json", {"choices": [{"finish_reason": "stop", "message": {"content": '{"clips": []}'}}]})
    assert not audit_outline_trace(tmp_path, "coverage", pc_contracts.validate_outline)["valid"]
    second["messages"][0]["content"] = "完整提纲：" + outline
    write(tmp_path / "http/002-request.json", second)
    assert audit_outline_trace(tmp_path, "coverage", pc_contracts.validate_outline)["valid"]
    write(tmp_path / "http/001-response.json", {"choices": [{"finish_reason": "length", "message": {"content": "", "reasoning": outline}}]})
    assert not audit_outline_trace(tmp_path, "coverage", pc_contracts.validate_outline)["valid"]
