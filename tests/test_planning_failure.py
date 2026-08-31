import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.config import Settings
from novel_manga.models import Character, Episode, NovelDocument, StoryBible
from novel_manga.pipeline import NovelPipeline
from novel_manga.planner import EpisodePlanningFailed


def test_planning_failure_is_persisted_and_never_authorizes_media(tmp_path: Path) -> None:
    settings = Settings(output_root=tmp_path, planner_max_revisions=1)
    episode = Episode(
        index=1,
        source_title="第一章",
        source_text="楚焱走上广场。",
        text_count=8,
        source_start=0,
        source_end=8,
    )
    novel = NovelDocument(
        novel_id="novel-1",
        title="测试小说",
        source_path=tmp_path / "novel.txt",
        text=episode.source_text,
        episodes=[episode],
        chaptered=True,
    )
    bible = StoryBible(
        novel_title=novel.title,
        genre="玄幻",
        visual_style="3D国漫",
        palette="暖金",
        characters=[
            Character(name="楚焱", appearance="黑发青年", wardrobe="深蓝长袍")
        ],
        locations=["楚家广场"],
        style_fingerprint="style-v1",
    )
    calls = 0
    draft_root = tmp_path / novel.novel_id / "script_drafts/episode_001"

    class Planner:
        @staticmethod
        def plan_episode_bundle(*args, **kwargs):
            nonlocal calls
            calls += 1
            draft_root.mkdir(parents=True, exist_ok=True)
            (draft_root / "beat_003_attempt_02.json").write_text(
                '{"partial": true}',
                encoding="utf-8",
            )
            raise EpisodePlanningFailed(
                "beat retry budget exhausted",
                episode_index=1,
                failed_stage="retention_beat_script",
                failed_beat_id="beat_003",
                attempts=2,
                elapsed_seconds=42.5,
                intermediate_root=draft_root,
            )

    pipeline = object.__new__(NovelPipeline)
    pipeline.settings = settings
    pipeline.providers = SimpleNamespace(
        media=SimpleNamespace(),
        planner=Planner(),
    )
    episode_dir = tmp_path / novel.novel_id / "novel-1_1"
    episode_dir.mkdir(parents=True)

    with pytest.raises(EpisodePlanningFailed, match="budget exhausted"):
        pipeline._load_or_build_plan(novel, episode, bible, episode_dir, None)

    failure_path = episode_dir / "planning_failed.json"
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["status"] == "planning_failed"
    assert failure["failed_beat_id"] == "beat_003"
    assert failure["attempts"] == 2
    assert failure["media_authorized"] is False
    assert failure["intermediate_artifacts"] == ["beat_003_attempt_02.json"]
    assert not (episode_dir / "production_plan.json").exists()
    assert not list(episode_dir.glob("*.mp4"))

    with pytest.raises(EpisodePlanningFailed, match="budget exhausted"):
        pipeline._load_or_build_plan(novel, episode, bible, episode_dir, None)
    assert calls == 1


def test_planning_timeout_defaults_to_ten_minutes() -> None:
    assert Settings().planning_timeout_seconds == 600.0
