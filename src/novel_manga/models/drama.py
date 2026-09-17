from __future__ import annotations
import re
from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator



class EpisodeMode(StrEnum):
    CHOICE = "choice_episode"
    PRESSURE = "pressure_episode"



class CharacterAwareness(BaseModel):
    character_name: str
    awareness: str = Field(pattern=r"^(knows|suspects|misled|unaware)$")
    belief: str = Field(default="", max_length=240)



class InformationState(BaseModel):
    """Who knows one source-grounded fact and how that gap creates drama."""

    fact_id: str = Field(pattern=r"^fact_\d{3}$")
    statement: str = Field(min_length=1, max_length=300)
    truth_status: str = Field(pattern=r"^(confirmed|potential|misread)$")
    viewer_awareness: str = Field(pattern=r"^(knows|suspects|misled|unaware)$")
    character_awareness: list[CharacterAwareness] = Field(default_factory=list)
    dramatic_use: str = Field(
        pattern=(
            r"^(viewer_leads|character_leads|simultaneous_reveal|"
            r"misunderstanding|withheld)$"
        )
    )
    source_event_ids: list[str] = Field(min_length=1)
    source_quote: str = Field(min_length=1, max_length=500)
    reveal_beat_id: str = ""



class CharacterDramaticState(BaseModel):
    social_status: str = "未明确"
    relationship_state: str = "未明确"
    power_level: str = "未明确"
    emotional_state: str = "未明确"
    confidence_state: str = "未明确"
    costume_state: str = "沿用角色资产"



class CharacterStateDelta(BaseModel):
    """A current-episode state transition, separate from permanent identity assets."""

    character_name: str
    event_ids: list[str] = Field(min_length=1)
    before: CharacterDramaticState
    after: CharacterDramaticState
    source_quote: str = Field(min_length=1, max_length=500)
    visual_consequence: str = Field(min_length=1, max_length=240)
    performance_consequence: str = Field(min_length=1, max_length=240)



class RetentionBeat(BaseModel):
    beat_id: str = Field(pattern=r"^beat_\d{3}$")
    function: str = Field(
        pattern=r"^(hook|question|pressure|escalation|payoff|reversal|cliffhanger)$"
    )
    target_start_ratio: float = Field(ge=0.0, le=1.0)
    target_end_ratio: float = Field(ge=0.0, le=1.0)
    audience_question: str = Field(min_length=1, max_length=240)
    promise: str = Field(min_length=1, max_length=240)
    new_information_fact_ids: list[str] = Field(default_factory=list)
    emotional_shift: str = Field(min_length=1, max_length=200)
    event_ids: list[str] = Field(min_length=1)
    shot_indexes: list[int] = Field(default_factory=list)
    source_quote: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_window(self) -> "RetentionBeat":
        if self.target_end_ratio < self.target_start_ratio:
            raise ValueError("retention beat end ratio must not precede its start ratio")
        return self



class RetentionPlan(BaseModel):
    target_duration_seconds: float = Field(default=60.0, ge=10.0, le=300.0)
    max_attention_gap_ratio: float = Field(default=0.25, gt=0.0, le=0.5)
    beats: list[RetentionBeat] = Field(min_length=4, max_length=8)
    ending_open_loop: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_retention_order(self) -> "RetentionPlan":
        ids = [beat.beat_id for beat in self.beats]
        if len(ids) != len(set(ids)):
            raise ValueError("retention beat ids must be unique")
        starts = [beat.target_start_ratio for beat in self.beats]
        if starts != sorted(starts):
            raise ValueError("retention beats must be ordered by target_start_ratio")
        return self



class ShowrunnerPlan(BaseModel):
    """Commercial short-drama decisions made before shot execution."""

    planning_mode: str = Field(
        default="planner", pattern=r"^(planner|inferred_fallback)$"
    )
    retention: RetentionPlan
    information_states: list[InformationState] = Field(default_factory=list, max_length=12)
    character_state_deltas: list[CharacterStateDelta] = Field(
        default_factory=list, max_length=12
    )
    episode_mode: EpisodeMode = EpisodeMode.PRESSURE
    protagonist_choice: str = Field(default="", max_length=240)
    choice_source_quote: str = Field(default="", max_length=500)
    cost_paid: str = Field(default="", max_length=240)
    cost_source_quote: str = Field(default="", max_length=500)
    opposition: "EpisodeOpposition | None" = None



class EpisodeContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = "episode-contract-v1"
    episode_index: int = Field(ge=1)
    development_version: str = Field(pattern=r"^v\d{3}$")
    arc_position: str
    pressure_loop: str
    protagonist_default_strategy: str
    strategy_creates_problem: str
    pressure_step: str
    setup_obligation_ids: list[str] = Field(default_factory=list)
    payoff_obligation_ids: list[str] = Field(default_factory=list)
    allowed_event_ids: list[str] = Field(min_length=1)
    allowed_information_fact_ids: list[str] = Field(default_factory=list)
    retention_beat_ids: list[str] = Field(min_length=1)
    required_close_state: str
    episode_mode: EpisodeMode = EpisodeMode.PRESSURE
    protagonist_choice: str = ""
    cost_paid: str = ""
    opposition: EpisodeOpposition | None = None



class EpisodeDramaturgy(BaseModel):
    """Source-grounded short-drama intent before sentence-level shots exist."""

    genre_engine: str = Field(min_length=1, max_length=80)
    dramatic_question: str = Field(min_length=1, max_length=240)
    cold_open: str = Field(min_length=1, max_length=240)
    cold_open_source_quote: str = Field(min_length=1, max_length=500)
    status_before: str = Field(min_length=1, max_length=240)
    status_after: str = Field(min_length=1, max_length=240)
    conflict_beats: list[str] = Field(min_length=1, max_length=6)
    reveal_order: list[str] = Field(default_factory=list, max_length=8)
    cliffhanger: str = Field(min_length=1, max_length=240)
    narration_budget_ratio: float = Field(default=0.2, ge=0.0, le=0.5)
    episode_mode: EpisodeMode = EpisodeMode.PRESSURE
    protagonist_choice: str = Field(default="", max_length=240)
    choice_source_quote: str = Field(default="", max_length=500)
    cost_paid: str = Field(default="", max_length=240)
    cost_source_quote: str = Field(default="", max_length=500)
    opposition: "EpisodeOpposition | None" = None



class EpisodeOpposition(BaseModel):
    opponent_name: str = Field(min_length=1, max_length=80)
    goal: str = Field(min_length=1, max_length=240)
    tactic: str = Field(min_length=1, max_length=240)
    source_event_ids: list[str] = Field(min_length=1)


# Resolve the existing forward references within this model group.
CharacterAwareness.model_rebuild()
InformationState.model_rebuild()
CharacterDramaticState.model_rebuild()
CharacterStateDelta.model_rebuild()
RetentionBeat.model_rebuild()
RetentionPlan.model_rebuild()
ShowrunnerPlan.model_rebuild()
EpisodeContract.model_rebuild()
EpisodeDramaturgy.model_rebuild()
EpisodeOpposition.model_rebuild()
