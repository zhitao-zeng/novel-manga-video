"""Per-episode media inputs and state; existing report dictionaries stay unchanged."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from ..config import Settings
from novel_manga.models.bible import StoryBible
from .asset_style import AssetStyle


@dataclass
class ClipRuntime:
    generated: bool | None = None
    softened: bool = False
    prescreened: bool = False
    repaired: bool = False
    compliance: bool = False


@dataclass
class RenderOptions:
    cache_only: bool = False
    first_pass_only: bool = False
    first_pass_clips: set | None = None
    prepared_requests_only: bool = False
    fast: bool = False
    free_retries: bool = False
    inflight: int = 0
    max_attempts: int = 2
    moderation_repair: bool = True
    prescreen: bool = False
    voice_budget: float | None = None
    workers: int = 1
    expressions: bool = True


@dataclass
class RenderState:
    approved_cached: dict = field(default_factory=dict)
    blocked_clips: dict = field(default_factory=dict)
    remaining: dict = field(default_factory=dict)
    ok_assets: set = field(default_factory=set)
    requested_workers: int = 1
    black_checks: set = field(default_factory=set)
    speech_checks: set = field(default_factory=set)
    clips: dict[str, ClipRuntime] = field(default_factory=dict)

    def clip(self, clip_id: str) -> ClipRuntime:
        return self.clips.setdefault(clip_id, ClipRuntime())


@dataclass
class RenderContext:
    options: RenderOptions = field(default_factory=RenderOptions)
    state: RenderState = field(default_factory=RenderState)
    aliases: dict = field(default_factory=dict)
    asr_helper: Path | None = None
    asr_python: str = ""
    asset_style: AssetStyle = field(default_factory=AssetStyle)
    bible: StoryBible | None = None
    chat_screen: dict = field(default_factory=dict)
    clip_plan: dict = field(default_factory=dict)
    episode_dir: Path | None = None
    feedback: dict = field(default_factory=dict)
    frame_spec: dict = field(default_factory=dict)
    novel_dir: Path | None = None
    source_novel_dir: Path | None = None
    profile: dict = field(default_factory=dict)
    protected_terms: list = field(default_factory=list)
    provider: Any = None
    renderer: Any = None
    script: dict = field(default_factory=dict)
    settings: Settings | None = None
    softening_rules: list | None = None
    work: Path | None = None
