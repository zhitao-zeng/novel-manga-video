import json
from pathlib import Path

import pytest

from novel_manga.config import Settings
from novel_manga.models import (
    ChapterDiagnosis,
    ChapterEvent,
    Character,
    Episode,
    NovelDocument,
    SeriesState,
    SeriesDevelopmentPlan,
    ShowrunnerPlan,
    StoryBible,
)
from novel_manga.planner import (
    OpenAICompatiblePlanner,
    _current_projection_context,
    _normalize_current_episode_state_quotes,
    _validate_series_development,
    plan_episode_contract,
)
from novel_manga.script_planning import validate_series_state


def _fixture() -> tuple[NovelDocument, StoryBible, list[ChapterDiagnosis]]:
    episodes = [
        Episode(
            index=index,
            source_title=f"第{index}章",
            source_text=text,
            text_count=len(text),
            source_start=0,
            source_end=len(text),
        )
        for index, text in enumerate(("甲挡住木门。", "门后藏着第二章秘密。"), 1)
    ]
    novel = NovelDocument(
        novel_id="series-test",
        title="系列测试",
        source_path=Path("series.txt"),
        text="\n".join(episode.source_text for episode in episodes),
        episodes=episodes,
        chaptered=True,
    )
    bible = StoryBible(
        novel_title=novel.title,
        genre="悬疑",
        visual_style="3D国漫",
        palette="冷青",
        characters=[Character(name="甲", appearance="黑发青年", wardrobe="蓝袍")],
        locations=["门前"],
        style_fingerprint="series-style",
    )
    diagnoses = [
        ChapterDiagnosis(
            source_chapter=episode.source_title,
            density="sparse",
            core_event=episode.source_text,
            chapter_start_state=f"第{episode.index}章开始",
            chapter_end_state=f"第{episode.index}章结束",
            episode_state_change=f"第{episode.index}章状态变化",
            strongest_hook_candidate=episode.source_text,
            hook_source_quote=episode.source_text,
            ending_type="consequence",
            events=[
                ChapterEvent(
                    event_id="event_001",
                    order=1,
                    description=episode.source_text,
                    source_quote=episode.source_text,
                    importance="critical",
                    narrative_role="resolution",
                    characters=["甲"],
                )
            ],
        )
        for episode in episodes
    ]
    return novel, bible, diagnoses


def _development() -> SeriesDevelopmentPlan:
    return SeriesDevelopmentPlan.model_validate(
        {
            "development_version": "v001",
            "novel_title": "系列测试",
            "engine": {
                "pressure_loop": "甲每次挡门都会招来更强质疑",
                "protagonist_default_strategy": "先控制风险",
                "strategy_creates_problem": "控制让同伴更不信任他",
                "escalation_ladder": ["异响", "质疑", "破门"],
                "termination_condition": "甲公开真相并承担后果",
            },
            "relationship_pressure_network": [],
            "obligations": [],
            "chapter_projections": [
                {
                    "episode_index": 1,
                    "source_chapter": "第1章",
                    "arc_position": "引擎建立",
                    "pressure_step": "甲第一次挡门",
                    "allowed_event_ids": ["event_001"],
                    "allowed_reveal_event_ids": ["event_001"],
                    "required_close_state": "第1章结束",
                },
                {
                    "episode_index": 2,
                    "source_chapter": "第2章",
                    "arc_position": "真相逼近",
                    "pressure_step": "第二章秘密首次出现",
                    "allowed_event_ids": ["event_001"],
                    "allowed_reveal_event_ids": ["event_001"],
                    "required_close_state": "第2章结束",
                },
            ],
        }
    )


def _showrunner() -> ShowrunnerPlan:
    functions = ("hook", "question", "payoff", "cliffhanger")
    source = "甲挡住木门。"
    return ShowrunnerPlan.model_validate(
        {
            "retention": {
                "beats": [
                    {
                        "beat_id": f"beat_{index:03d}",
                        "function": function,
                        "target_start_ratio": (index - 1) * 0.25,
                        "target_end_ratio": index * 0.25,
                        "audience_question": "甲为何挡门？",
                        "promise": "当前章内推进",
                        "new_information_fact_ids": ["fact_001"] if index == 1 else [],
                        "emotional_shift": "压力上升",
                        "event_ids": ["event_001"],
                        "source_quote": source,
                    }
                    for index, function in enumerate(functions, 1)
                ],
                "ending_open_loop": "门外是谁？",
            },
            "information_states": [
                {
                    "fact_id": "fact_001",
                    "statement": "甲挡住木门",
                    "truth_status": "confirmed",
                    "viewer_awareness": "knows",
                    "dramatic_use": "simultaneous_reveal",
                    "source_event_ids": ["event_001"],
                    "source_quote": source,
                    "reveal_beat_id": "beat_001",
                }
            ],
        }
    )


def test_series_development_is_grounded_and_episode_context_excludes_future_projection() -> None:
    novel, bible, diagnoses = _fixture()
    development = _validate_series_development(
        _development().model_dump(mode="json"),
        novel=novel,
        bible=bible,
        diagnoses=diagnoses,
        development_version="v001",
    )

    current_context = json.dumps(
        _current_projection_context(development, 1),
        ensure_ascii=False,
    )
    assert "甲第一次挡门" in current_context
    assert "第二章秘密首次出现" not in current_context
    assert "第2章结束" not in current_context

    contract = plan_episode_contract(
        development=development,
        diagnosis=diagnoses[0],
        showrunner=_showrunner(),
        episode_index=1,
    )
    assert contract.allowed_event_ids == ["event_001"]
    assert contract.allowed_information_fact_ids == ["fact_001"]
    assert contract.required_close_state == "第1章结束"


def test_series_development_normalizes_projection_copy_fields() -> None:
    novel, bible, diagnoses = _fixture()
    raw = _development().model_dump(mode="json")
    raw["chapter_projections"][0].update(
        {
            "episode_index": 9,
            "source_chapter": "模型改写的章名",
            "allowed_event_ids": [],
            "allowed_reveal_event_ids": [],
            "required_close_state": "模型概括的结束状态",
        }
    )

    development = _validate_series_development(
        raw,
        novel=novel,
        bible=bible,
        diagnoses=diagnoses,
        development_version="v001",
    )

    projection = development.chapter_projections[0]
    assert projection.episode_index == 1
    assert projection.source_chapter == diagnoses[0].source_chapter
    assert projection.allowed_event_ids == ["event_001"]
    assert projection.allowed_reveal_event_ids == ["event_001"]
    assert projection.required_close_state == diagnoses[0].chapter_end_state


def test_series_state_normalizes_only_current_episode_evidence_quote() -> None:
    episode = Episode(
        index=1,
        source_title="第1章",
        source_text="甲走到门前。甲抬手挡住木门。",
        text_count=16,
        source_start=0,
        source_end=16,
    )
    raw = {
        "current_episode": 1,
        "timeline": [
            {
                "statement": "甲决定阻止别人开门",
                "source_episode": 1,
                "source_quote": "甲挡住了门",
                "certainty": "confirmed",
            }
        ],
    }

    normalized, changes = _normalize_current_episode_state_quotes(raw, episode)
    state = validate_series_state(
        SeriesState.model_validate(normalized),
        episode,
        None,
    )

    assert changes == ["timeline.0"]
    assert state.timeline[0].statement == "甲决定阻止别人开门"
    assert state.timeline[0].source_quote in episode.source_text


def test_series_projection_and_episode_contract_reject_cross_chapter_permissions() -> None:
    novel, bible, diagnoses = _fixture()
    broken = _development().model_copy(deep=True)
    broken.chapter_projections[0].allowed_reveal_event_ids = ["event_999"]

    with pytest.raises(ValueError, match="another chapter"):
        _validate_series_development(
            broken.model_dump(mode="json"),
            novel=novel,
            bible=bible,
            diagnoses=diagnoses,
            development_version="v001",
        )

    showrunner = _showrunner().model_copy(deep=True)
    showrunner.retention.beats[0].event_ids = ["event_999"]
    with pytest.raises(ValueError, match="outside the current projection"):
        plan_episode_contract(
            development=_development(),
            diagnosis=diagnoses[0],
            showrunner=showrunner,
            episode_index=1,
        )


def test_showrunner_prompt_reads_engine_and_only_the_current_projection() -> None:
    novel, bible, diagnoses = _fixture()
    development = _development()
    planner = OpenAICompatiblePlanner(
        Settings(
            planner_backend="openai-compatible",
            llm_base_url="http://127.0.0.1:1/v1",
            llm_api_key="fixture",
            planner_max_revisions=0,
        )
    )
    captured = {}

    def capture(system, user, repair=None, *, token_budget=None):
        captured["system"] = system
        captured["user"] = user
        raise ValueError("capture")

    planner._json = capture  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="plan_showrunner remained invalid"):
        planner._plan_showrunner(
            novel.episodes[0],
            diagnoses[0],
            bible,
            None,
            development,
        )

    assert "甲每次挡门都会招来更强质疑" in captured["user"]
    assert "甲第一次挡门" in captured["user"]
    assert "第二章秘密首次出现" not in captured["user"]
    assert "第2章结束" not in captured["user"]
