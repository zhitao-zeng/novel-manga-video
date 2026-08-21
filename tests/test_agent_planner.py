from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_manga.config import Settings
from novel_manga.ingest import read_novel
from novel_manga.models import Character, StoryBible
from novel_manga.planner import OpenAICompatiblePlanner, _loads_json_object


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "choices": [{"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}]
        }


class _Client:
    def __init__(self, payloads: list[dict]):
        self.payloads = iter(payloads)
        self.requests: list[dict] = []

    def post(self, url: str, *, headers: dict, json: dict) -> _Response:
        self.requests.append({"url": url, "headers": headers, "json": json})
        return _Response(next(self.payloads))


def test_model_json_parser_repairs_only_missing_comma() -> None:
    assert _loads_json_object('{"a": {"b": 1} "c": 2}') == {
        "a": {"b": 1},
        "c": 2,
    }


def _plan(source: str, speaker: str) -> dict:
    return {
        "video_title": "门外来客",
        "hook": "林晚听见门外脚步声。",
        "summary": "林晚阻止开门。",
        "shots": [
            {
                "index": 1,
                "narration": "林晚低声说：“不要开门。”",
                "subtitle": "不要开门。",
                "visual_prompt": "门外的脚步声逼近，人物神情警惕",
                "motion_prompt": "镜头缓慢推近",
                "characters": [speaker],
                "location": "门外",
                "source_quote": source,
                "performance_plan": {
                    "objective": "林晚从警惕转为坚决，不是静态摆拍",
                    "start_state": "林晚听到脚步声前身体尚未转向门口",
                    "motion_beats": [
                        {
                            "phase": "opening",
                            "trigger": "门外脚步声逼近",
                            "action": "眼睛先转向门口，随后侧头",
                            "reaction": "肩膀绷紧，身体重心后移",
                            "expression_transition": "从平静转为警惕"
                        },
                        {
                            "phase": "resolution",
                            "trigger": "确认门外有危险",
                            "action": "抬手示意不要开门",
                            "reaction": "身体挡向门口",
                            "expression_transition": "从警惕转为坚决"
                        }
                    ],
                    "end_state": "林晚挡在门前，目光坚定"
                },
                "camera_plan": {
                    "start_position": "林晚左后方中景",
                    "camera_beats": [
                        {
                            "phase": "opening",
                            "trajectory": "随林晚转头向右横移",
                            "framing": "由双人中景转为林晚胸像",
                            "parallax": "近处门框移动快于远处走廊"
                        },
                        {
                            "phase": "resolution",
                            "trajectory": "沿短弧线移到林晚右前方并减速",
                            "framing": "停在四分之三近景",
                            "parallax": "门框、人物和走廊形成三层视差"
                        }
                    ],
                    "end_position": "林晚右前方稳定近景"
                },
                "turns": [
                    {
                        "role": speaker,
                        "speaker_name": speaker,
                        "text": "不要开门。",
                        "speaking": True,
                        "source_quote": source,
                    }
                ],
            }
        ],
    }


def _fixture(tmp_path: Path):
    source_path = tmp_path / "novel.txt"
    source_path.write_text("第一章 门外\n林晚低声说：“不要开门。”", encoding="utf-8")
    novel = read_novel(source_path, novel_id="agent-test", title="测试小说")
    bible = StoryBible(
        novel_title="测试小说",
        genre="悬疑",
        visual_style="二维国漫",
        palette="青蓝",
        characters=[
            Character(name="林晚", role="主角", appearance="黑发少女", wardrobe="蓝色风衣")
        ],
        locations=["门外"],
        style_fingerprint="fixture",
    )
    settings = Settings(
        planner_backend="openai-compatible",
        llm_base_url="http://127.0.0.1:18001/v1",
        llm_api_key="local",
        llm_model="qwen-local",
        planner_max_revisions=2,
    )
    return novel, bible, settings


def test_openai_compatible_planner_repairs_invalid_plan_before_media(tmp_path: Path) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    client = _Client([_plan(source, "陌生人"), _plan(source, "林晚")])
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    plan = planner.plan_episode(novel, novel.episodes[0], bible)

    assert plan.shots[0].turns[0].speaker_name == "林晚"
    assert len(client.requests) == 2
    messages = client.requests[1]["json"]["messages"]
    assert messages[-1]["role"] == "user"
    assert "校验反馈" in messages[-1]["content"]
    assert "StoryBible character names" in messages[-1]["content"]


def test_openai_planner_converts_anonymous_crowd_to_offscreen_narration(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    payload = _plan(source, "路人甲")
    client = _Client([payload])
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    plan = planner.plan_episode(novel, novel.episodes[0], bible)

    turn = plan.shots[0].turns[0]
    assert turn.speaking is False
    assert turn.role == "narrator"
    assert turn.speaker_name == "旁白"
    assert plan.shots[0].characters == []
    assert len(client.requests) == 1


def test_openai_compatible_planner_stops_after_bounded_revisions(tmp_path: Path) -> None:
    novel, bible, settings = _fixture(tmp_path)
    settings = Settings(**{**settings.__dict__, "planner_max_revisions": 1})
    source = novel.episodes[0].source_text
    client = _Client([_plan(source, "陌生人"), _plan(source, "陌生人")])
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    with pytest.raises(ValueError, match=r"remained invalid after 2 attempt\(s\)"):
        planner.plan_episode(novel, novel.episodes[0], bible)

    assert len(client.requests) == 2


def test_openai_compatible_planner_grounds_approximate_quote_without_repair(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    payload = _plan(source, "林晚")
    payload["shots"][0]["source_quote"] = "林晚提醒门外有危险。"
    payload["shots"][0]["turns"][0]["source_quote"] = "林晚提醒门外有危险。"
    client = _Client([payload])
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    plan = planner.plan_episode(novel, novel.episodes[0], bible)

    assert plan.shots[0].source_quote in source
    assert plan.shots[0].turns[0].source_quote in source
    assert len(client.requests) == 1


def test_openai_planner_normalizes_location_and_interrupted_dialogue(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = "第一章 门外\n林晚说：\u201c不要走。\u201d她停了一下，又说：\u201c快关门。\u201d"
    novel = novel.model_copy(
        update={
            "text": source,
            "episodes": [
                novel.episodes[0].model_copy(
                    update={"source_text": source, "text_count": len(source)}
                )
            ],
        }
    )
    bible = bible.model_copy(update={"locations": ["昏暗的门内走廊"]})
    payload = _plan(source, "林晚")
    shot = payload["shots"][0]
    shot["location"] = "门内走廊"
    shot["source_quote"] = "林晚说：\u201c不要走。\u201d她停了一下，又说：\u201c快关门。\u201d"
    shot["turns"] = [
        {
            "role": "林晚",
            "speaker_name": "林晚",
            "text": "不要走。快关门。",
            "speaking": True,
            "source_quote": "不要走。快关门。",
        },
        {
            "role": "林晚",
            "speaker_name": "林晚",
            "text": "这里怎么突然这么黑？",
            "speaking": True,
            "source_quote": shot["source_quote"],
        },
    ]
    client = _Client([payload])
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    plan = planner.plan_episode(novel, novel.episodes[0], bible)

    assert plan.shots[0].location == "昏暗的门内走廊"
    assert plan.shots[0].turns[0].speaking is True
    assert "她停了一下" in plan.shots[0].turns[0].source_quote
    assert plan.shots[0].turns[1].speaking is False
    assert plan.shots[0].turns[1].role == "narrator"


def test_openai_compatible_planner_can_bound_qwen_output(tmp_path: Path) -> None:
    novel, bible, settings = _fixture(tmp_path)
    settings = Settings(
        **{
            **settings.__dict__,
            "llm_max_tokens": 4096,
            "llm_disable_thinking": True,
        }
    )
    source = novel.episodes[0].source_text
    client = _Client([_plan(source, "林晚")])
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    planner.plan_episode(novel, novel.episodes[0], bible)

    request = client.requests[0]["json"]
    assert request["max_tokens"] == 4096
    assert request["chat_template_kwargs"] == {"enable_thinking": False}


def test_planner_revision_budget_is_hard_capped() -> None:
    Settings(planner_max_revisions=6).validate()
    with pytest.raises(ValueError, match="NOVEL_PLANNER_MAX_REVISIONS"):
        Settings(planner_max_revisions=7).validate()


def test_openai_planner_runs_diagnosis_script_review_and_state_stages(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    episode = novel.episodes[0]
    source = episode.source_text
    diagnosis = {
        "source_chapter": episode.source_title,
        "density": "sparse",
        "core_event": "林晚阻止开门",
        "chapter_start_state": "林晚在门内听见动静",
        "chapter_end_state": "林晚明确阻止开门",
        "episode_state_change": "危险从未知变为被人物警觉",
        "strongest_hook_candidate": "门外脚步逼近",
        "hook_source_quote": source,
        "ending_type": "decision",
        "events": [
            {
                "event_id": "event_001",
                "order": 1,
                "description": "林晚低声阻止开门",
                "source_quote": source,
                "importance": "critical",
                "narrative_role": "resolution",
                "characters": ["林晚"],
            }
        ],
    }
    plan = _plan(source, "林晚")
    plan["shots"][0]["event_ids"] = ["event_001"]
    plan["shots"][0]["turns"].insert(
        0,
        {
            "role": "narrator",
            "speaker_name": "旁白",
            "text": "门外脚步逼近，林晚立即警觉。",
            "speaking": False,
            "source_quote": source,
        },
    )
    plan["adaptation_ledger"] = [
        {
            "event_id": "event_001",
            "disposition": "preserved",
            "shot_indexes": [1],
            "rationale": "用动作和原文对白完整表现",
        }
    ]
    review = {
        "passed": True,
        "script_char_count": 1,
        "shot_count": 1,
        "turn_count": 1,
        "critical_event_coverage": 1,
        "causal_chain_complete": True,
        "character_introductions_complete": True,
        "opening_no_spoiler": True,
        "ending_at_chapter_boundary": True,
        "future_content_used": False,
        "issues": [],
    }
    evidence = {
        "statement": "林晚明确阻止开门",
        "source_episode": 1,
        "source_quote": source,
    }
    state = {
        "current_episode": 1,
        "timeline": [evidence],
        "characters": [
            {
                "name": "林晚",
                "current_location": "门内",
                "emotional_state": "警觉",
                "current_goal": "阻止开门",
                "evidence": evidence,
            }
        ],
        "previous_episode_end": {
            "location": "门内",
            "action": "林晚阻止开门",
            "final_visual": "林晚挡在门前",
            "evidence": evidence,
        },
    }
    client = _Client([diagnosis, plan, review, state])
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    bundle = planner.plan_episode_bundle(novel, episode, bible)

    assert bundle.quality_report.passed is True
    assert bundle.quality_report.script_char_count > review["script_char_count"]
    assert bundle.updated_series_state.characters[0].current_goal == "阻止开门"
    draft_root = settings.output_root / novel.novel_id / "script_drafts" / "episode_001"
    assert (draft_root / "chapter_diagnosis.json").exists()
    assert (draft_root / "draft_01.json").exists()
    assert (draft_root / "draft_01_validation.json").exists()
    assert len(client.requests) == 4
    assert [request["json"]["max_tokens"] for request in client.requests] == [
        6000,
        settings.llm_max_tokens,
        4000,
        5000,
    ]
    prompts = [request["json"]["messages"][0]["content"] for request in client.requests]
    assert "事实编辑" in prompts[0]
    assert "逐章编剧" in prompts[1]
    assert "独立的漫剧剧本审稿人" in prompts[2]
    assert "连续剧状态管理员" in prompts[3]
