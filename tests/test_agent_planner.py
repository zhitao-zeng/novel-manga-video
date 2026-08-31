from __future__ import annotations

import json
from pathlib import Path

import pytest

import novel_manga.planner as planner_module
from novel_manga.config import Settings
from novel_manga.ingest import read_novel
from novel_manga.models import (
    Character,
    EpisodeDramaturgy,
    EpisodePlan,
    ScriptQualityReport,
    ScriptTurn,
    ShotIntent,
    StoryBible,
    TurnDelivery,
)
from novel_manga.planner import OpenAICompatiblePlanner, _loads_json_object
from novel_manga.script_planning import deterministic_chapter_diagnosis
from novel_manga.script_planning import evaluate_script_quality, normalize_chronological_plan


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


def test_dialogue_source_span_accepts_a_long_verbatim_substring() -> None:
    planner = OpenAICompatiblePlanner(
        Settings(
            planner_backend="openai-compatible",
            llm_base_url="http://127.0.0.1:18001/v1",
            llm_api_key="local",
        )
    )
    source = '"战之气：七段！"\n"楚媚，战之气：七段！级别：高级！"'

    span = planner._dialogue_source_span("战之气：七段！级别：高级！", source)

    assert span is not None
    assert '"楚媚，战之气：七段！级别：高级！"' in span


def test_script_turn_normalizes_chinese_narrator_role() -> None:
    turn = ScriptTurn.model_validate(
        {
            "role": "旁白",
            "speaker_name": "旁白",
            "text": "三年前，他的力量突然消失。",
            "speaking": True,
            "delivery_mode": "narration",
            "source_quote": "三年之前，他接受到了最残酷的打击。",
            "derivation": "derived",
        }
    )

    assert turn.role == "narrator"
    assert turn.speaking is False
    assert turn.delivery_mode == TurnDelivery.NARRATION


def test_script_turn_recovers_real_speaker_from_mislabeled_narrator_role() -> None:
    turn = ScriptTurn.model_validate(
        {
            "role": "narrator",
            "speaker_name": "测验员",
            "text": "战之气：七段！",
            "speaking": True,
            "delivery_mode": "visible_dialogue",
            "source_quote": '"战之气：七段！"',
        }
    )

    assert turn.role == "测验员"
    assert turn.speaking is True


@pytest.mark.parametrize(
    ("retention_function", "shot_function"),
    [
        ("hook", "establish"),
        ("question", "withhold"),
        ("escalation", "pressure"),
        ("reversal", "reveal"),
        ("climax", "payoff"),
    ],
)
def test_shot_intent_normalizes_retention_function_aliases(
    retention_function: str,
    shot_function: str,
) -> None:
    intent = ShotIntent.model_validate({"dramatic_function": retention_function})

    assert intent.dramatic_function == shot_function


def test_short_drama_normalization_uses_actual_cold_open_as_hook(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    plan = EpisodePlan.model_validate(_plan(source, "林晚")).model_copy(
        update={
            "creative_profile": "short-drama-adaptive-v1",
            "hook": "把章末反转也写进了hook",
            "dramaturgy": EpisodeDramaturgy(
                genre_engine="suspense",
                dramatic_question="门外是谁？",
                cold_open="林晚听见门外脚步逼近。",
                cold_open_source_quote=source,
                status_before="尚未确认危险",
                status_after="林晚决定阻止开门",
                conflict_beats=["脚步逼近"],
                cliffhanger="门外的人会进来吗？",
                narration_budget_ratio=0.2,
            ),
        }
    )

    normalized = normalize_chronological_plan(
        plan,
        deterministic_chapter_diagnosis(novel.episodes[0]),
        novel.episodes[0],
    )

    assert normalized.hook == "林晚听见门外脚步逼近。"


@pytest.mark.parametrize(
    "review_code", ["TURN_LENGTH_OVERFLOW", "TURN_TOO_LONG"]
)
def test_independent_review_turn_length_issue_defers_to_deterministic_count(
    tmp_path: Path,
    review_code: str,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    plan = EpisodePlan.model_validate(_plan(source, "林晚"))
    qualitative = ScriptQualityReport(
        passed=False,
        script_char_count=68,
        shot_count=1,
        turn_count=1,
        critical_event_coverage=1.0,
        causal_chain_complete=True,
        character_introductions_complete=True,
        opening_no_spoiler=True,
        ending_at_chapter_boundary=True,
        issues=[
            {
                "code": review_code,
                "severity": "blocking",
                "message": "reviewer estimated 68 chars",
                "shot_indexes": [1],
                "event_ids": [],
            }
        ],
    )

    report = evaluate_script_quality(
        plan,
        deterministic_chapter_diagnosis(novel.episodes[0]),
        novel.episodes[0],
        qualitative=qualitative,
    )

    assert review_code not in {issue.code for issue in report.issues}


def test_independent_review_false_without_blocker_is_report_only(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    plan = EpisodePlan.model_validate(_plan(source, "林晚"))
    qualitative = ScriptQualityReport(
        passed=False,
        script_char_count=5,
        shot_count=1,
        turn_count=1,
        critical_event_coverage=1.0,
        causal_chain_complete=True,
        character_introductions_complete=True,
        opening_no_spoiler=True,
        ending_at_chapter_boundary=True,
        issues=[],
    )

    report = evaluate_script_quality(
        plan,
        deterministic_chapter_diagnosis(novel.episodes[0]),
        novel.episodes[0],
        qualitative=qualitative,
    )

    issue = next(
        item
        for item in report.issues
        if item.code == "independent_review_nonactionable_fail"
    )
    assert issue.severity == "warning"
    assert issue.gate_level == "craft"


def test_real_turn_length_overflow_keeps_independent_review_issue(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    payload = _plan(source, "林晚")
    payload["shots"][0]["turns"][0].update(
        {
            "text": "不" * 61,
            "source_quote": source,
            "derivation": "derived",
        }
    )
    plan = EpisodePlan.model_validate(payload)
    qualitative = ScriptQualityReport(
        passed=False,
        script_char_count=61,
        shot_count=1,
        turn_count=1,
        critical_event_coverage=1.0,
        causal_chain_complete=True,
        character_introductions_complete=True,
        opening_no_spoiler=True,
        ending_at_chapter_boundary=True,
        issues=[
            {
                "code": "TURN_LENGTH_OVERFLOW",
                "severity": "blocking",
                "message": "real overflow",
                "shot_indexes": [1],
                "event_ids": [],
            }
        ],
    )

    report = evaluate_script_quality(
        plan,
        deterministic_chapter_diagnosis(novel.episodes[0]),
        novel.episodes[0],
        qualitative=qualitative,
    )

    assert "TURN_LENGTH_OVERFLOW" in {issue.code for issue in report.issues}


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
        creative_profile="faithful-chronological-v1",
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


def test_screenplay_retries_switch_from_repair_to_independent_resample(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    settings = Settings(
        **{
            **settings.__dict__,
            "output_root": tmp_path / "outputs",
            "planner_max_revisions": 3,
        }
    )
    source = novel.episodes[0].source_text
    client = _Client([_plan(source, "陌生人") for _ in range(4)])
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]
    planner._diagnose_episode = (  # type: ignore[method-assign]
        lambda episode, bible, previous_state: deterministic_chapter_diagnosis(episode)
    )

    with pytest.raises(ValueError, match="script quality gate remained invalid"):
        planner.plan_episode_bundle(novel, novel.episodes[0], bible)

    assert len(client.requests) == 4
    initial, repair, resample, resample_repair = [
        request["json"] for request in client.requests
    ]
    assert initial["temperature"] == 0.2
    assert any(message["role"] == "assistant" for message in repair["messages"])
    assert repair["temperature"] == 0.2
    assert all(message["role"] != "assistant" for message in resample["messages"])
    assert "重新独立创作" in resample["messages"][-1]["content"]
    assert resample["temperature"] == 1.0
    assert resample["top_p"] == 0.95
    assert isinstance(resample["seed"], int)
    assert any(message["role"] == "assistant" for message in resample_repair["messages"])
    assert resample_repair["temperature"] == 0.2


def test_screenplay_json_parse_errors_stay_inside_the_retry_budget(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    settings = Settings(
        **{
            **settings.__dict__,
            "output_root": tmp_path / "outputs",
            "planner_max_revisions": 1,
        }
    )
    source = novel.episodes[0].source_text
    responses: list[dict | ValueError] = [
        json.JSONDecodeError("Expecting ',' delimiter", "{}", 1),
        _plan(source, "陌生人"),
    ]
    planner = OpenAICompatiblePlanner(settings)
    planner._diagnose_episode = (  # type: ignore[method-assign]
        lambda episode, bible, previous_state: deterministic_chapter_diagnosis(episode)
    )

    def fake_json(*args, **kwargs):
        response = responses.pop(0)
        if isinstance(response, ValueError):
            raise response
        return response

    planner._json = fake_json  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="script quality gate remained invalid"):
        planner.plan_episode_bundle(novel, novel.episodes[0], bible)

    validation = json.loads(
        (
            tmp_path
            / "outputs/agent-test/script_drafts/episode_001/draft_01_validation.json"
        ).read_text(encoding="utf-8")
    )
    assert validation["passed"] is False
    assert validation["errors"][0]["type"] == "JSONDecodeError"
    assert responses == []


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
        }
    ]
    client = _Client([payload])
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    plan = planner.plan_episode(novel, novel.episodes[0], bible)

    assert plan.shots[0].location == "昏暗的门内走廊"
    assert plan.shots[0].turns[0].speaking is True
    assert "她停了一下" in plan.shots[0].turns[0].source_quote


def test_openai_planner_keeps_untraceable_dialogue_with_its_speaker(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    payload = _plan(source, "林晚")
    payload["shots"][0]["turns"][0]["text"] = "这里怎么突然这么黑？"
    planner = OpenAICompatiblePlanner(settings)

    plan = planner._validate_episode_data(payload, novel.episodes[0], bible)

    turn = plan.shots[0].turns[0]
    assert turn.role == "林晚"
    assert turn.speaker_name == "林晚"
    assert turn.speaking is True


def test_openai_planner_removes_stage_direction_already_encoded_by_delivery_mode(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    payload = _plan(source, "林晚")
    payload["shots"][0]["turns"][0].update(
        {
            "text": "（内心声）不要开门。",
            "speaking": False,
            "delivery_mode": "inner_voice",
        }
    )
    planner = OpenAICompatiblePlanner(settings)

    plan = planner._validate_episode_data(payload, novel.episodes[0], bible)

    assert plan.shots[0].turns[0].text == "不要开门。"
    assert plan.shots[0].turns[0].delivery_mode == TurnDelivery.INNER_VOICE


def test_openai_planner_restores_one_near_verbatim_line_to_source(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = "第一章 门外\n林晚说：“以前你曾经说过，要能放下，才能拿起。”"
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
    payload = _plan(source, "林晚")
    payload["shots"][0]["turns"][0].update(
        {
            "text": "以前你说过，要能放下，才能拿起。",
            "source_quote": "林晚说：“以前你曾经说过，要能放下，才能拿起。”",
            "derivation": "verbatim",
        }
    )
    planner = OpenAICompatiblePlanner(settings)

    plan = planner._validate_episode_data(payload, novel.episodes[0], bible)

    assert plan.shots[0].turns[0].text == "以前你曾经说过，要能放下，才能拿起。"
    assert plan.shots[0].turns[0].derivation.value == "verbatim"


def test_openai_planner_anchors_abstract_flashback_between_one_scene(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    payload = _plan(source, "林晚")
    shots = []
    for index, location in enumerate(("门外", "回忆/虚空", "门外"), 1):
        shot = {**payload["shots"][0], "index": index, "location": location}
        shots.append(shot)
    payload["shots"] = shots
    planner = OpenAICompatiblePlanner(settings)

    plan = planner._canonicalize_characters(EpisodePlan.model_validate(payload), bible)

    assert [shot.location for shot in plan.shots] == ["门外", "门外", "门外"]


def test_openai_planner_anchors_flashback_to_its_trigger_scene(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    bible = bible.model_copy(update={"locations": ["门外", "室内"]})
    source = novel.episodes[0].source_text
    payload = _plan(source, "林晚")
    shots = []
    for index, location in enumerate(("门外", "回忆/虚空", "室内"), 1):
        shot = {**payload["shots"][0], "index": index, "location": location}
        shots.append(shot)
    payload["shots"] = shots
    planner = OpenAICompatiblePlanner(settings)

    plan = planner._canonicalize_characters(EpisodePlan.model_validate(payload), bible)

    assert plan.shots[1].location == "门外"


def test_openai_planner_anchors_adjacent_exterior_to_previous_asset(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    bible = bible.model_copy(update={"locations": ["家族广场"]})
    source = novel.episodes[0].source_text
    payload = _plan(source, "林晚")
    payload["shots"] = [
        {**payload["shots"][0], "index": 1, "location": "家族广场"},
        {**payload["shots"][0], "index": 2, "location": "广场外"},
    ]
    planner = OpenAICompatiblePlanner(settings)

    plan = planner._canonicalize_characters(EpisodePlan.model_validate(payload), bible)

    assert [shot.location for shot in plan.shots] == ["家族广场", "家族广场"]


def test_openai_planner_keeps_unrelated_unknown_location_for_validation(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    payload = _plan(source, "林晚")
    payload["shots"] = [
        {**payload["shots"][0], "index": 1, "location": "门外"},
        {**payload["shots"][0], "index": 2, "location": "陌生宫殿"},
    ]
    planner = OpenAICompatiblePlanner(settings)

    plan = planner._canonicalize_characters(EpisodePlan.model_validate(payload), bible)

    assert plan.shots[1].location == "陌生宫殿"


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


def test_semantic_repair_prompt_requires_observable_earlier_setup(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    client = _Client([{}])
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    planner._json(
        "system",
        "user",
        {
            "revision": 1,
            "previous_response": {"shots": []},
            "validation_errors": [
                {
                    "message": (
                        "MISSING_CAUSALITY at shot 8; "
                        "CHARACTER_MOTIVATION_UNCLEAR at shot 11"
                    )
                },
                {
                    "message": (
                        "causal_chain_broken: event_006 lacks event_005; "
                        "narrator_summarises_dialogue at shot 3"
                    )
                },
            ],
        },
    )

    repair_prompt = client.requests[0]["json"]["messages"][-1]["content"]
    assert "更小shot index" in repair_prompt
    assert "后置台词不能反向补足" in repair_prompt
    assert "不得只润色" in repair_prompt
    assert "前置event_id加入更小shot index" in repair_prompt
    assert "把原文对白逐条完整恢复给具体角色" in repair_prompt


def test_showrunner_prompt_binds_causal_prerequisites_to_first_twenty_percent(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    settings = Settings(
        **{**settings.__dict__, "planner_max_revisions": 0}
    )
    planner = OpenAICompatiblePlanner(settings)
    captured: dict[str, str] = {}

    def fail_after_capture(system, user, repair=None, *, token_budget=None):
        captured["system"] = system
        raise ValueError("capture only")

    planner._json = fail_after_capture  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="plan_showrunner remained invalid"):
        planner._plan_showrunner(
            novel.episodes[0],
            deterministic_chapter_diagnosis(novel.episodes[0]),
            bible,
            None,
        )

    assert "target_start_ratio<=0.20" in captured["system"]
    assert "不能只写在audience_question或promise" in captured["system"]
    assert "secondary branch只能在主角核心问题" in captured["system"]
    assert "行动之后的对白不能反向补足" in captured["system"]


def test_script_expansion_prompt_locks_existing_and_quoted_turns(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    plan = EpisodePlan.model_validate(_plan(source, "林晚"))
    expansion = {
        "shots": [
            {
                "shot_index": 1,
                "turns": [
                    turn.model_dump(mode="json") for turn in plan.shots[0].turns
                ],
            }
        ]
    }
    client = _Client([expansion])
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    planner._expand_script_turns(
        novel.episodes[0],
        bible,
        deterministic_chapter_diagnosis(novel.episodes[0]),
        plan,
        required_chars=1,
        previous_state=None,
    )

    system_prompt = client.requests[0]["json"]["messages"][0]["content"]
    assert "现有turn是事实与角色归属基线" in system_prompt
    assert "若过长、重复、书面化或导致逐字比例超限" in system_prompt
    assert "长对白可设置derivation=abridged" in system_prompt
    assert "只按原顺序删除完整标点子句" in system_prompt
    assert "连续拼接后必须等于所选完整子句" in system_prompt


def test_script_expansion_repairs_changes_to_existing_turns(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    plan = EpisodePlan.model_validate(_plan(source, "林晚"))
    original_turns = [
        turn.model_dump(mode="json") for turn in plan.shots[0].turns
    ]
    changed_turns = [{**original_turns[0], "text": "不要把门打开。"}]
    client = _Client(
        [
            {"shots": [{"shot_index": 1, "turns": changed_turns}]},
            {"shots": [{"shot_index": 1, "turns": original_turns}]},
        ]
    )
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    expanded = planner._expand_script_turns(
        novel.episodes[0],
        bible,
        deterministic_chapter_diagnosis(novel.episodes[0]),
        plan,
        required_chars=1,
        previous_state=None,
    )

    assert expanded.shots[0].turns == plan.shots[0].turns
    assert len(client.requests) == 2
    repair_prompt = client.requests[1]["json"]["messages"][-1]["content"]
    assert "must preserve every existing turn exactly" in repair_prompt


def test_script_expansion_repairs_a_still_short_patch(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    plan = EpisodePlan.model_validate(_plan(source, "林晚"))
    original_turns = [
        turn.model_dump(mode="json") for turn in plan.shots[0].turns
    ]
    added_turn = {
        "role": "narrator",
        "speaker_name": "旁白",
        "text": "门外脚步逼近。",
        "speaking": False,
        "delivery_mode": "narration",
        "emotion": "紧张",
        "source_quote": source,
        "derivation": "derived",
    }
    client = _Client(
        [
            {"shots": [{"shot_index": 1, "turns": original_turns}]},
            {
                "shots": [
                    {
                        "shot_index": 1,
                        "turns": [*original_turns, added_turn],
                    }
                ]
            },
        ]
    )
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    expanded = planner._expand_script_turns(
        novel.episodes[0],
        bible,
        deterministic_chapter_diagnosis(novel.episodes[0]),
        plan,
        required_chars=12,
        previous_state=None,
    )

    assert len(expanded.shots[0].turns) == 2
    assert len(client.requests) == 2
    repair_prompt = client.requests[1]["json"]["messages"][-1]["content"]
    assert "script expansion remains too short" in repair_prompt


def test_script_expansion_accepts_exact_source_restoration(
    tmp_path: Path,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = "第一章 门外\n林晚低声说：“你千万不要开门。”"
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
    payload = _plan(source, "林晚")
    payload["shots"][0]["turns"][0].update(
        {
            "text": "你千万不要打开门。",
            "source_quote": "林晚低声说：“你千万不要开门。”",
            "derivation": "derived",
        }
    )
    plan = EpisodePlan.model_validate(payload)
    restored_turn = {
        **plan.shots[0].turns[0].model_dump(mode="json"),
        "text": "你千万不要开门。",
        "derivation": "verbatim",
    }
    client = _Client(
        [{"shots": [{"shot_index": 1, "turns": [restored_turn]}]}]
    )
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]

    expanded = planner._expand_script_turns(
        novel.episodes[0],
        bible,
        deterministic_chapter_diagnosis(novel.episodes[0]),
        plan,
        required_chars=1,
        previous_state=None,
    )

    assert expanded.shots[0].turns[0].text == "你千万不要开门。"
    assert expanded.shots[0].turns[0].derivation.value == "verbatim"


def test_turn_attribution_patch_is_exact_and_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    plan = EpisodePlan.model_validate(_plan(source, "林晚"))
    failure = ScriptQualityReport(
        passed=False,
        script_char_count=5,
        shot_count=1,
        turn_count=1,
        critical_event_coverage=1.0,
        causal_chain_complete=True,
        character_introductions_complete=True,
        opening_no_spoiler=True,
        ending_at_chapter_boundary=True,
        issues=[
            {
                "code": "narrator_summarises_dialogue",
                "severity": "blocking",
                "message": "restore quoted dialogue",
                "shot_indexes": [1],
                "event_ids": [],
            }
        ],
    )
    success = failure.model_copy(update={"passed": True, "issues": []})
    original_turns = [
        turn.model_dump(mode="json") for turn in plan.shots[0].turns
    ]
    client = _Client(
        [
            {"shots": []},
            {"shots": [{"shot_index": 1, "turns": original_turns}]},
        ]
    )
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]
    monkeypatch.setattr(
        planner_module,
        "evaluate_script_quality",
        lambda *args, **kwargs: success,
    )

    repaired = planner._repair_turn_attribution(
        novel.episodes[0],
        bible,
        deterministic_chapter_diagnosis(novel.episodes[0]),
        plan,
        failure,
        None,
    )

    assert repaired.shots[0].turns == plan.shots[0].turns
    assert len(client.requests) == 2
    system_prompt = client.requests[0]["json"]["messages"][0]["content"]
    assert "只修输入列出的镜头turns" in system_prompt
    assert "必须返回一次" in system_prompt
    repair_prompt = client.requests[1]["json"]["messages"][-1]["content"]
    assert "at least 1 item" in repair_prompt


def test_review_content_patch_is_targeted_and_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    plan = EpisodePlan.model_validate(_plan(source, "林晚"))
    failure = ScriptQualityReport(
        passed=False,
        script_char_count=5,
        shot_count=1,
        turn_count=1,
        critical_event_coverage=1.0,
        causal_chain_complete=True,
        character_introductions_complete=True,
        opening_no_spoiler=True,
        ending_at_chapter_boundary=True,
        issues=[
            {
                "code": "CHARACTER_MOTIVATION_UNCLEAR",
                "severity": "blocking",
                "message": "先展示过去关系，再展示当前选择",
                "shot_indexes": [1],
                "event_ids": ["event_001"],
            }
        ],
    )
    success = failure.model_copy(update={"passed": True, "issues": []})
    client = _Client(
        [
            {"shots": [{"shot_index": 99, "motion_prompt": "wrong target"}]},
            {"shots": [{"shot_index": 1, "motion_prompt": "林晚先回头，再挡住门。"}]},
            {
                "shots": [
                    {
                        "shot_index": 1,
                        "motion_prompt": "林晚曾经信任对方，如今先回头，再挡住门。",
                    }
                ]
            },
        ]
    )
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]
    monkeypatch.setattr(
        planner_module,
        "evaluate_script_quality",
        lambda *args, **kwargs: success,
    )

    repaired = planner._repair_review_content(
        novel.episodes[0],
        bible,
        deterministic_chapter_diagnosis(novel.episodes[0]),
        plan,
        failure,
        None,
    )

    assert repaired.shots[0].motion_prompt == "林晚曾经信任对方，如今先回头，再挡住门。"
    assert len(client.requests) == 3
    system_prompt = client.requests[0]["json"]["messages"][0]["content"]
    assert "只允许shot_index以及turns" in system_prompt
    assert "不得修改event_ids" in system_prompt
    assert "具体年龄" in system_prompt
    assert "放在结果揭示之前" in system_prompt
    repair_prompt = client.requests[1]["json"]["messages"][-1]["content"]
    assert "non-target shot indexes" in repair_prompt
    relationship_prompt = client.requests[2]["json"]["messages"][-1]["content"]
    assert "must include current-chapter evidence of the past relationship" in relationship_prompt


def test_missing_causality_review_code_routes_to_targeted_content_patch() -> None:
    assert any(
        token in "MISSING_CAUSALITY"
        for token in planner_module.REVIEW_CONTENT_PATCH_TOKENS
    )


def test_review_content_patch_repairs_causal_context_in_an_earlier_shot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    novel, bible, settings = _fixture(tmp_path)
    source = novel.episodes[0].source_text
    payload = _plan(source, "林晚")
    payload["shots"] = [
        {**payload["shots"][0], "index": 1},
        {**payload["shots"][0], "index": 2},
    ]
    plan = EpisodePlan.model_validate(payload)
    failure = ScriptQualityReport(
        passed=False,
        script_char_count=10,
        shot_count=2,
        turn_count=2,
        critical_event_coverage=1.0,
        causal_chain_complete=True,
        character_introductions_complete=True,
        opening_no_spoiler=True,
        ending_at_chapter_boundary=True,
        issues=[
            {
                "code": "MISSING_CAUSAL_CONTEXT",
                "severity": "blocking",
                "message": "setup must precede reveal",
                "shot_indexes": [2],
                "event_ids": ["event_001"],
            }
        ],
    )
    success = failure.model_copy(update={"passed": True, "issues": []})
    client = _Client(
        [
            {"shots": [{"shot_index": 2, "motion_prompt": "too late"}]},
            {"shots": [{"shot_index": 1, "motion_prompt": "昔日天才"}]},
            {
                "shots": [
                    {
                        "shot_index": 1,
                        "motion_prompt": "十一岁凝聚气旋，成为百年最年轻战者",
                    }
                ]
            },
        ]
    )
    planner = OpenAICompatiblePlanner(settings)
    planner.client = client  # type: ignore[assignment]
    monkeypatch.setattr(
        planner_module,
        "evaluate_script_quality",
        lambda *args, **kwargs: success,
    )

    repaired = planner._repair_review_content(
        novel.episodes[0],
        bible,
        deterministic_chapter_diagnosis(novel.episodes[0]),
        plan,
        failure,
        None,
    )

    assert repaired.shots[0].motion_prompt == "十一岁凝聚气旋，成为百年最年轻战者"
    assert len(client.requests) == 3
    order_prompt = client.requests[1]["json"]["messages"][-1]["content"]
    assert "must edit an earlier shot than the reveal" in order_prompt
    detail_prompt = client.requests[2]["json"]["messages"][-1]["content"]
    assert "must add concrete age, level, mechanism" in detail_prompt


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
    assert "落笔前执行前置条件先行" in prompts[1]
    assert "后续对白不能反向补足" in prompts[1]
    assert "独立的漫剧剧本审稿人" in prompts[2]
    assert "连续剧状态管理员" in prompts[3]
