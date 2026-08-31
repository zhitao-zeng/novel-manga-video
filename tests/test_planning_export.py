from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_manga.config import Settings
from novel_manga.models import Character, EpisodePlan, ScriptTurn, Shot, StoryBible
from novel_manga.planner import OpenAICompatiblePlanner
from novel_manga.planning_export import compile_planning_bundle, export_planning_bundle


def test_export_planning_bundle_uses_no_media_provider(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("第一章 起点\n\n林舟推开门，看见桌上的旧钥匙。", encoding="utf-8")
    output = tmp_path / "plans"
    settings = Settings(provider="mock", planner_backend="deterministic", output_root=output)

    result = export_planning_bundle(settings, source, novel_id="demo", title="测试小说")

    manifest_path = Path(result["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["contract"] == "novel-manga-planning/v3"
    assert manifest["episode_count"] == 1
    assert manifest["planner"]["backend"] == "deterministic"
    assert manifest["planner"]["credentials_persisted"] is False
    assert (manifest_path.parent / manifest["story_bible"]).is_file()
    assert (manifest_path.parent / manifest["episodes"][0]["plan"]).is_file()
    assert (manifest_path.parent / manifest["episodes"][0]["diagnosis"]).is_file()
    assert (manifest_path.parent / manifest["episodes"][0]["script_quality"]).is_file()
    assert (manifest_path.parent / manifest["episodes"][0]["updated_series_state"]).is_file()
    quality = json.loads(
        (manifest_path.parent / manifest["episodes"][0]["script_quality"]).read_text(
            encoding="utf-8"
        )
    )
    assert quality["passed"] is True


def test_openai_planner_requires_endpoint_and_key() -> None:
    with pytest.raises(ValueError, match="NOVEL_LLM_BASE_URL"):
        Settings(
            provider="mock",
            planner_backend="openai-compatible",
        ).validate()


def test_request_timeout_can_be_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOVEL_REQUEST_TIMEOUT", "600")
    settings = Settings.from_env(provider="mock", admission_mode="preview")
    assert settings.request_timeout == 600


def test_openai_planner_normalizes_character_aliases() -> None:
    bible = StoryBible(
        novel_title="测试",
        genre="悬疑",
        visual_style="国漫",
        palette="暗色",
        style_fingerprint="test",
        characters=[
            Character(
                name="周明瑞/克莱恩·莫雷蒂",
                role="主角",
                appearance="黑发青年",
                wardrobe="白衬衫黑马甲",
            )
        ],
    )
    plan = EpisodePlan(
        video_title="测试",
        hook="测试",
        summary="测试",
        shots=[
            Shot(
                index=1,
                narration="测试",
                subtitle="测试",
                visual_prompt="测试",
                motion_prompt="测试",
                characters=["周明瑞"],
                source_quote="测试",
                turns=[
                    ScriptTurn(
                        role="周明瑞",
                        speaker_name="周明瑞",
                        text="测试",
                        speaking=True,
                        source_quote="测试",
                    )
                ],
            )
        ],
    )

    planner = object.__new__(OpenAICompatiblePlanner)
    normalized = planner._canonicalize_characters(plan, bible)
    assert normalized.shots[0].characters == ["周明瑞/克莱恩·莫雷蒂"]
    assert normalized.shots[0].turns[0].speaker_name == "周明瑞/克莱恩·莫雷蒂"
    assert normalized.shots[0].turns[0].role == "周明瑞/克莱恩·莫雷蒂"

    locked_bible = bible.model_copy(
        update={
            "characters": [
                Character(
                    name="克莱恩·莫雷蒂",
                    role="主角",
                    appearance="黑发青年",
                    wardrobe="白衬衫黑马甲",
                )
            ]
        }
    )
    locked = planner._canonicalize_characters(normalized, locked_bible)
    assert locked.shots[0].characters == ["克莱恩·莫雷蒂"]
    assert locked.shots[0].turns[0].speaker_name == "克莱恩·莫雷蒂"


def test_compile_planning_bundle_materializes_downstream_contract(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("第一章 起点\n\n林舟推开门，看见桌上的旧钥匙。", encoding="utf-8")
    output = tmp_path / "plans"
    settings = Settings(provider="mock", planner_backend="deterministic", output_root=output)
    exported = export_planning_bundle(settings, source, novel_id="demo", title="测试小说")

    result = compile_planning_bundle(
        source,
        exported["output_directory"],
        novel_id="demo",
        title="测试小说",
    )

    assert result["contract"] == "novel-manga-production/v1"
    assert result["media_generated"] is False
    assert result["episodes"][0]["unit_count"] >= 1
    production_path = Path(result["bundle"]) / result["episodes"][0]["production_plan"]
    production = json.loads(production_path.read_text(encoding="utf-8"))
    assert production["video_id"] == "demo_1"
    assert "audio_path" not in production["units"][0]
