import tomllib
from pathlib import Path

import pytest

from novel_manga import __version__
from novel_manga.models import EpisodeStatus, SubmissionManifest, VideoRecord
from novel_manga.safety import safe_visual_prompt, scan_source


def _record() -> VideoRecord:
    return VideoRecord(
        video_id="1_1",
        video_title="第一章",
        video_cover="1_1/1_1_cover.jpeg",
        ending_screen="1_1/1_1_ending.jpeg",
        video_file="1_1/1_1.mp4",
        text_count=4078,
        status=EpisodeStatus.SUCCEEDED,
    )


def test_manifest_count_is_strict():
    with pytest.raises(ValueError):
        SubmissionManifest(novel_id="1", novel_title="测试", video_count=2, videos=[_record()])


def test_high_risk_source_is_blocked_and_prompts_are_softened():
    findings = scan_source("反派计划开膛破肚，场面血肉横飞。")
    assert {finding.category for finding in findings} == {"graphic_violence"}
    prompt = safe_visual_prompt("地上有血迹，他要杀死敌人")
    assert "血迹" not in prompt
    assert "杀死" not in prompt
    assert "无血腥" in prompt


def test_package_version_matches_project_metadata():
    metadata = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert __version__ == metadata["project"]["version"]
