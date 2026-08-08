from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class EpisodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


class Episode(BaseModel):
    index: int = Field(ge=1)
    source_title: str
    source_text: str
    text_count: int = Field(ge=1)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)


class NovelDocument(BaseModel):
    novel_id: str
    title: str
    source_path: Path
    text: str
    episodes: list[Episode]
    chaptered: bool


class Character(BaseModel):
    name: str
    role: str = "配角"
    gender: str = "未知"
    age: str = "成年"
    appearance: str
    wardrobe: str


class StoryBible(BaseModel):
    novel_title: str
    genre: str
    visual_style: str
    palette: str
    typography: str = "粗体无衬线中文字体，白字黑描边"
    characters: list[Character] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    continuity_rules: list[str] = Field(default_factory=list)
    style_fingerprint: str


class ScriptTurn(BaseModel):
    """One locked narration or visible-speaker utterance inside a shot."""

    role: str = "narrator"
    speaker_name: str = "旁白"
    text: str = Field(min_length=1, max_length=500)
    speaking: bool = False
    emotion: str = "克制自然"
    source_quote: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_speaker(self) -> "ScriptTurn":
        if self.role == "narrator" and self.speaking:
            raise ValueError("narrator turns cannot be visible speaking turns")
        if self.role != "narrator" and not self.speaker_name.strip():
            raise ValueError("visible speaking turns require speaker_name")
        return self


class Shot(BaseModel):
    index: int = Field(ge=1)
    narration: str = Field(min_length=1, max_length=80)
    subtitle: str = Field(min_length=1, max_length=80)
    visual_prompt: str
    motion_prompt: str
    characters: list[str] = Field(default_factory=list)
    location: str = ""
    source_quote: str = Field(min_length=1, max_length=120)
    scene_job: str = "推进"
    shot_scale: str = "中近景"
    turns: list[ScriptTurn] = Field(default_factory=list)


class EpisodePlan(BaseModel):
    video_title: str
    hook: str
    summary: str
    shots: list[Shot] = Field(min_length=1)
    next_preview: str = "敬请期待下一集"

    @model_validator(mode="after")
    def validate_shot_order(self) -> "EpisodePlan":
        expected = list(range(1, len(self.shots) + 1))
        actual = [shot.index for shot in self.shots]
        if actual != expected:
            raise ValueError(f"shot indexes must be consecutive: {actual}")
        return self


class MediaPaths(BaseModel):
    video: str
    video_cover: str
    ending_screen: str
    plan: str
    trace: str
    qc_report: str


class VideoRecord(BaseModel):
    video_id: str
    video_title: str
    video_cover: str
    ending_screen: str
    video_file: str
    text_count: int
    status: EpisodeStatus
    error: str | None = None


class SubmissionManifest(BaseModel):
    novel_id: str
    novel_title: str
    video_count: int
    videos: list[VideoRecord]

    @model_validator(mode="after")
    def validate_count(self) -> "SubmissionManifest":
        if self.video_count != len(self.videos):
            raise ValueError("video_count must equal len(videos)")
        return self
