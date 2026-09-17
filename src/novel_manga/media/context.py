"""Per-episode media inputs and state; existing report dictionaries stay unchanged."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict
from ..config import Settings
from novel_manga.models.bible import StoryBible
from .asset_style import AssetStyle


@dataclass
class RenderContext:
    _approved_cached: dict = field(default_factory=dict)
    _blocked_clips: dict = field(default_factory=dict)
    _managed_remaining: dict = field(default_factory=dict)
    _ok_assets: set = field(default_factory=set)
    _workers_arg: int = 1
    aliases: dict = field(default_factory=dict)
    asr_helper: Path | None = None
    asr_python: str = ""
    asset_style: AssetStyle = field(default_factory=AssetStyle)
    bible: StoryBible | None = None
    black_checks: set = field(default_factory=set)
    cache_only: bool = False
    chat_screen: dict = field(default_factory=dict)
    clip_plan: dict = field(default_factory=dict)
    episode_dir: Path | None = None
    fast: bool = False
    feedback: dict = field(default_factory=dict)
    frame_spec: dict = field(default_factory=dict)
    free_retries: bool = False
    inflight: int = 0
    max_attempts: int = 2
    moderation_repair: bool = True
    novel_dir: Path | None = None
    prescreen: bool = False
    profile: dict = field(default_factory=dict)
    protected_terms: list = field(default_factory=list)
    provider: Any = None
    renderer: Any = None
    script: dict = field(default_factory=dict)
    settings: Settings | None = None
    softening_rules: list | None = None
    speech_checks: set = field(default_factory=set)
    voice_budget: float | None = None
    work: Path | None = None
    workers: int = 1


class ClipResult(TypedDict, total=False):
    clip_id: str
    attempts: list[dict]
    selected: dict | None
    error: str
    retake_error: str
    blocked: list[str]


class AssemblyResult(TypedDict, total=False):
    final_video: str
    cover: str
    ending: str
    ass: str
    duration: float
    subtitle_events: int
    media_qc_passed: bool
    max_hold_seconds: float
    thin_passed: bool
    media_qc: dict
    silent_outro_seconds: float
    pending_publish: bool
