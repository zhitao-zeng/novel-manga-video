from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_manga.config import Settings
from novel_manga.ingest import read_novel
from novel_manga.models import Character, StoryBible
from novel_manga.planner import OpenAICompatiblePlanner


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


def test_planner_revision_budget_is_hard_capped() -> None:
    with pytest.raises(ValueError, match="NOVEL_PLANNER_MAX_REVISIONS"):
        Settings(planner_max_revisions=3).validate()
