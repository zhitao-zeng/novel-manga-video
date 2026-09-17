from __future__ import annotations
import re
from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator

from novel_manga.models.dialogue import ScriptTurn
from novel_manga.models.directing import CameraPlan, HandoffState, PerformancePlan, SceneAudioPlan, ShotIntent, VisualStrategy
from novel_manga.models.drama import EpisodeContract, EpisodeDramaturgy, ShowrunnerPlan


class AdaptationLedgerItem(BaseModel):
    event_id: str = Field(pattern=r"^event_\d{3}$")
    disposition: str = Field(
        pattern=r"^(preserved|compressed|merged|externalized|removed)$"
    )
    shot_indexes: list[int] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=240)



class Shot(BaseModel):
    index: int = Field(ge=1)
    narration: str = Field(min_length=1, max_length=80)
    subtitle: str = Field(min_length=1, max_length=80)
    visual_prompt: str
    motion_prompt: str
    characters: list[str] = Field(default_factory=list)
    extras: list[str] = Field(default_factory=list)  # unnamed people in frame, drawn from a short description, no card
    listeners: list[str] = Field(default_factory=list)  # present but back to camera / out of frame while someone else speaks
    location: str = ""
    source_quote: str = Field(min_length=1, max_length=500)
    scene_job: str = "推进"
    change: str = Field(default="", max_length=240)
    event_ids: list[str] = Field(default_factory=list)
    shot_scale: str = "中近景"
    turns: list[ScriptTurn] = Field(default_factory=list)
    performance_plan: PerformancePlan | None = None
    camera_plan: CameraPlan | None = None
    visual_strategy: VisualStrategy = VisualStrategy.AUTO
    keyframe_reasons: list[str] = Field(default_factory=list)
    shot_intent: ShotIntent = Field(default_factory=ShotIntent)
    audio_plan: SceneAudioPlan = Field(default_factory=SceneAudioPlan)
    script_open_state: HandoffState | None = None
    script_close_state: HandoffState | None = None



class EpisodePlan(BaseModel):
    video_title: str
    hook: str
    summary: str
    shots: list[Shot] = Field(min_length=1)
    next_preview: str = "敬请期待下一集"
    adaptation_ledger: list[AdaptationLedgerItem] = Field(default_factory=list)
    creative_profile: str = "faithful-chronological-v1"
    dramaturgy: EpisodeDramaturgy | None = None
    showrunner_plan: ShowrunnerPlan | None = None
    episode_contract: EpisodeContract | None = None

    @model_validator(mode="after")
    def validate_shot_order(self) -> "EpisodePlan":
        expected = list(range(1, len(self.shots) + 1))
        actual = [shot.index for shot in self.shots]
        if actual != expected:
            raise ValueError(f"shot indexes must be consecutive: {actual}")
        return self


# Resolve the existing forward references within this model group.
AdaptationLedgerItem.model_rebuild()
Shot.model_rebuild()
EpisodePlan.model_rebuild()
