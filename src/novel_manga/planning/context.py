"""Mutable state owned by one chapter-planning operation, never by a module."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from .constants import DEFAULT_SYSTEM_PROMPT, DEFAULT_ANONYMOUS_SPEAKERS

@dataclass
class PlannerContext:
    policy: str = "thin-chapter-plan-v13-bounded-repair"
    clip_seconds_max: float = 30.0
    short_clips: bool = False
    clip_range: tuple[int, int] = (3, 4)
    stage_range: tuple[int, int] = (4, 6)
    max_clip_seconds: float = 30.0
    episode_seconds_target: float = 90.0
    episode_seconds_max: float = 105.0
    episode_seconds_min: float = 0.0
    spoken_range: tuple[int, int] = (220, 300)
    strict_plan: bool = False
    anonymous_speakers: list[str] = field(default_factory=lambda: list(DEFAULT_ANONYMOUS_SPEAKERS))
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    separate_pairs: list[tuple[str,str]] = field(default_factory=list)
    aliases: dict[str,str] = field(default_factory=dict)
    fast_tier: bool = False
    chat_self: str = ""
    chat_card_mode: bool = True
    text_on_props_gate: bool = True
    entity_forms: dict[str,list[str]] = field(default_factory=dict)
    entity_tiers: dict[str,str] = field(default_factory=dict)
    entity_generic: dict[str,bool] = field(default_factory=dict)
    forms_index: dict = field(default_factory=dict)
    max_skipped: int = 0
    story_method: str = ""
    story_blueprint: dict = field(default_factory=dict)
    # Scene-exit question/answer pairs from the source (text.handoff_pairs): the question must be
    # heard whatever the outline did with it - the pair is the cut itself.
    handoff_pairs: list = field(default_factory=list)
    # Set when --bind-storyboard binds a sheet a person wrote: its cuts and its length are
    # the author's, and compressing them is the one thing binding exists to avoid.
    authored_storyboard: bool = False
    # Whether the video service will refuse a picture on content grounds.  The paid platforms do,
    # so a plan is checked for what they refuse before any money is spent on it.  The local H3 does
    # not: sent a knife coming down and blood spreading into standing water, it drew exactly that
    # (在美漫当心灵导师的日子 ch1 shot 12, 2026-09-22).  A gate kept for a refusal that cannot happen
    # only costs the picture it was guarding.
    renderer_moderates: bool = True
    method_artifacts: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> PlannerContext:
        from novel_manga.config import LOCAL_VIDEO_MODELS
        raw = os.environ.get("NOVEL_CLIP_SECONDS_MAX", "30")
        cap = float(raw or 30)
        short = cap <= 15
        video_model = os.environ.get("NOVEL_VIDEO_MODEL", os.environ.get("PHANROUTER_VIDEO_MODEL", "")).strip()
        return cls(policy="thin-chapter-plan-v13-bounded-repair" + ("-15s" if raw.strip() in {"15", "15.0"} else ""),
                   clip_seconds_max=cap, short_clips=short, max_clip_seconds=cap,
                   clip_range=(6, 8) if short else (3, 4), stage_range=(2, 3) if short else (4, 6),
                   strict_plan=os.environ.get("NOVEL_PLAN_STRICT", "").strip() == "1",
                   renderer_moderates=video_model not in LOCAL_VIDEO_MODELS)
