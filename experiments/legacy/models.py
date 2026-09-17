from __future__ import annotations
import re
from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator

from novel_manga.models.dialogue import ScriptTurn
from novel_manga.models.directing import CameraPlan, HandoffState, PerformancePlan, SceneAudioPlan, ShotIntent, VisualStrategy
from novel_manga.models.drama import EpisodeContract
from novel_manga.models.episode import EpisodePlan


class EpisodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"



class ChapterEvent(BaseModel):
    """One source-grounded event that the adaptation must account for."""

    event_id: str = Field(pattern=r"^event_\d{3}$")
    order: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=240)
    source_quote: str = Field(min_length=1, max_length=500)
    importance: str = Field(pattern=r"^(critical|supporting|texture)$")
    narrative_role: str = Field(
        pattern=r"^(setup|development|turning_point|climax|resolution)$"
    )
    characters: list[str] = Field(default_factory=list)
    causes: list[str] = Field(default_factory=list)
    state_change: str = Field(default="", max_length=240)
    potential_foreshadowing: bool = False



class ChapterDiagnosis(BaseModel):
    """Model-neutral chapter diagnosis produced before screenplay writing."""

    source_chapter: str
    density: str = Field(pattern=r"^(sparse|balanced|dense)$")
    core_event: str = Field(min_length=1, max_length=300)
    chapter_start_state: str = Field(min_length=1, max_length=300)
    chapter_end_state: str = Field(min_length=1, max_length=300)
    episode_state_change: str = Field(min_length=1, max_length=300)
    strongest_hook_candidate: str = Field(min_length=1, max_length=300)
    hook_source_quote: str = Field(min_length=1, max_length=500)
    ending_type: str = Field(
        pattern=r"^(action|secret|decision|consequence|relationship|emotion)$"
    )
    potential_foreshadowing: list[str] = Field(default_factory=list)
    events: list[ChapterEvent] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_events(self) -> "ChapterDiagnosis":
        ids = [event.event_id for event in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("chapter event ids must be unique")
        if [event.order for event in self.events] != list(range(1, len(self.events) + 1)):
            raise ValueError("chapter event order must be consecutive")
        known: set[str] = set()
        for event in self.events:
            unknown = set(event.causes) - known
            if unknown:
                raise ValueError(
                    f"{event.event_id} causes must reference earlier events: {sorted(unknown)}"
                )
            known.add(event.event_id)
        return self



class QualityGateLevel(StrEnum):
    STRUCTURAL = "structural"
    REVIEWED = "reviewed"
    CRAFT = "craft"



class ScriptReviewIssue(BaseModel):
    code: str
    severity: str = Field(pattern=r"^(blocking|warning)$")
    message: str = Field(min_length=1, max_length=500)
    shot_indexes: list[int] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    gate_level: QualityGateLevel = QualityGateLevel.STRUCTURAL



class ScriptQualityReport(BaseModel):
    policy_revision: str = "novel-manga-script-v7-active-drama"
    passed: bool
    script_char_count: int = Field(ge=0)
    shot_count: int = Field(ge=0)
    turn_count: int = Field(ge=0)
    critical_event_coverage: float = Field(ge=0.0, le=1.0)
    causal_chain_complete: bool
    character_introductions_complete: bool
    opening_no_spoiler: bool
    ending_at_chapter_boundary: bool
    future_content_used: bool = False
    max_turn_char_count: int = Field(default=0, ge=0)
    target_overflow_turn_count: int = Field(default=0, ge=0)
    hard_overflow_turn_count: int = Field(default=0, ge=0)
    narration_char_count: int = Field(default=0, ge=0)
    narration_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    narration_budget_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    cold_open_grounded: bool = True
    camera_move_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    retention_beat_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    max_attention_gap_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    information_fact_grounding: float = Field(default=0.0, ge=0.0, le=1.0)
    character_delta_grounding: float = Field(default=0.0, ge=0.0, le=1.0)
    character_delta_grounding_floor: float = Field(default=0.0, ge=0.0, le=1.0)
    shot_intent_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    audio_beat_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    verbatim_turn_count: int = Field(default=0, ge=0)
    abridged_turn_count: int = Field(default=0, ge=0)
    derived_turn_count: int = Field(default=0, ge=0)
    source_anchored_turn_count: int = Field(default=0, ge=0)
    source_anchored_char_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    externalization_device_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    derived_serves_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    derived_char_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    verbatim_turn_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    verbatim_turn_ratio_max: float = Field(default=1.0, ge=0.0, le=1.0)
    shot_change_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    protagonist_name: str = ""
    protagonist_agency_shot_count: int = Field(default=0, ge=0)
    protagonist_agency_floor: int = Field(default=0, ge=0)
    named_conflict_shot_count: int = Field(default=0, ge=0)
    named_conflict_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    visible_cliffhanger: bool = False
    density_reference_min_shots: int = Field(default=22, ge=0)
    density_reference_max_shots: int = Field(default=36, ge=0)
    expected_shots_from_retention: int = Field(default=0, ge=0)
    density_target_script_chars: int = Field(default=0, ge=0)
    density_target_turns: int = Field(default=0, ge=0)
    density_within_reference: bool = False
    structural_blocker_count: int = Field(default=0, ge=0)
    reviewed_blocker_count: int = Field(default=0, ge=0)
    craft_warning_count: int = Field(default=0, ge=0)
    issues: list[ScriptReviewIssue] = Field(default_factory=list)



class GroundedStateFact(BaseModel):
    statement: str = Field(min_length=1, max_length=300)
    source_episode: int = Field(ge=1)
    source_quote: str = Field(min_length=1, max_length=500)
    certainty: str = Field(default="confirmed", pattern=r"^(confirmed|potential)$")



class CharacterEpisodeState(BaseModel):
    name: str
    current_location: str = "未知"
    current_outfit: str = "沿用角色资产"
    physical_state: str = "正常"
    emotional_state: str = "未明确"
    current_goal: str = "未明确"
    social_status: str = "未明确"
    relationship_state: str = "未明确"
    power_level: str = "未明确"
    confidence_state: str = "未明确"
    costume_state: str = "沿用角色资产"
    evidence: GroundedStateFact
    known_information: list[GroundedStateFact] = Field(default_factory=list)



class RelationshipState(BaseModel):
    people: list[str] = Field(min_length=2, max_length=4)
    status: str
    power_balance: str = "未明确"
    evidence: GroundedStateFact



class PropState(BaseModel):
    name: str
    holder: str = "未知"
    state: str
    evidence: GroundedStateFact



class StoryLoop(BaseModel):
    loop_id: str
    question: str
    status: str = Field(pattern=r"^(open|resolved|potential)$")
    opened_episode: int = Field(ge=1)
    resolved_episode: int | None = Field(default=None, ge=1)
    evidence: GroundedStateFact



class EpisodeEndState(BaseModel):
    location: str
    action: str
    final_line: str = ""
    final_visual: str
    evidence: GroundedStateFact



class SeriesInformationState(BaseModel):
    """Cross-episode knowledge state with evidence that survives event-id reuse."""

    fact_key: str
    statement: str = Field(min_length=1, max_length=300)
    viewer_awareness: str = Field(pattern=r"^(knows|suspects|misled|unaware)$")
    character_awareness: dict[str, str] = Field(default_factory=dict)
    dramatic_use: str = Field(
        pattern=(
            r"^(viewer_leads|character_leads|simultaneous_reveal|"
            r"misunderstanding|withheld)$"
        )
    )
    evidence: GroundedStateFact

    @model_validator(mode="after")
    def validate_character_awareness(self) -> "SeriesInformationState":
        allowed = {"knows", "suspects", "misled", "unaware"}
        invalid = {
            name: awareness
            for name, awareness in self.character_awareness.items()
            if awareness not in allowed
        }
        if invalid:
            raise ValueError(f"invalid character awareness values: {invalid}")
        return self



class SeriesState(BaseModel):
    """Compact dynamic memory carried from episode N to episode N+1."""

    schema_version: int = 1
    current_episode: int = Field(ge=0)
    timeline: list[GroundedStateFact] = Field(default_factory=list)
    characters: list[CharacterEpisodeState] = Field(default_factory=list)
    relationships: list[RelationshipState] = Field(default_factory=list)
    props: list[PropState] = Field(default_factory=list)
    information_states: list[SeriesInformationState] = Field(default_factory=list)
    open_loops: list[StoryLoop] = Field(default_factory=list)
    resolved_loops: list[StoryLoop] = Field(default_factory=list)
    potential_foreshadowing: list[GroundedStateFact] = Field(default_factory=list)
    previous_episode_end: EpisodeEndState | None = None



class ScriptTurnPatch(BaseModel):
    shot_index: int = Field(ge=1)
    turns: list[ScriptTurn] = Field(min_length=1)



class ScriptExpansion(BaseModel):
    shots: list[ScriptTurnPatch] = Field(min_length=1)



class StoryEngine(BaseModel):
    pressure_loop: str = Field(min_length=1, max_length=300)
    protagonist_default_strategy: str = Field(min_length=1, max_length=240)
    strategy_creates_problem: str = Field(min_length=1, max_length=300)
    escalation_ladder: list[str] = Field(min_length=3, max_length=10)
    termination_condition: str = Field(min_length=1, max_length=300)



class RelationshipPressureEdge(BaseModel):
    people: list[str] = Field(min_length=2, max_length=3)
    pressure: str = Field(min_length=1, max_length=240)
    leverage: str = Field(min_length=1, max_length=240)
    escalation: str = Field(min_length=1, max_length=240)



class SetupPayoffObligation(BaseModel):
    obligation_id: str = Field(pattern=r"^obligation_\d{3}$")
    setup_episode: int = Field(ge=1)
    payoff_episode_min: int = Field(ge=1)
    payoff_episode_max: int = Field(ge=1)
    setup_function: str = Field(min_length=1, max_length=240)
    payoff_function: str = Field(min_length=1, max_length=240)
    source_event_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_payoff_window(self) -> "SetupPayoffObligation":
        if self.payoff_episode_min < self.setup_episode:
            raise ValueError("payoff window cannot precede setup episode")
        if self.payoff_episode_max < self.payoff_episode_min:
            raise ValueError("payoff window end cannot precede its start")
        return self



class ChapterProjection(BaseModel):
    episode_index: int = Field(ge=1)
    source_chapter: str = Field(min_length=1, max_length=200)
    arc_position: str = Field(min_length=1, max_length=200)
    pressure_step: str = Field(min_length=1, max_length=300)
    allowed_event_ids: list[str] = Field(min_length=1)
    allowed_reveal_event_ids: list[str] = Field(default_factory=list)
    setup_obligation_ids: list[str] = Field(default_factory=list)
    payoff_obligation_ids: list[str] = Field(default_factory=list)
    required_close_state: str = Field(min_length=1, max_length=300)



class SeriesDevelopmentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    development_version: str = Field(pattern=r"^v\d{3}$")
    novel_title: str
    engine: StoryEngine
    relationship_pressure_network: list[RelationshipPressureEdge] = Field(
        default_factory=list,
        max_length=20,
    )
    obligations: list[SetupPayoffObligation] = Field(default_factory=list, max_length=30)
    chapter_projections: list[ChapterProjection] = Field(min_length=1)



class SeriesDevelopmentReview(BaseModel):
    review_revision: str = "series-development-review-v1"
    passed: bool
    engine_coherent: bool
    projections_grounded: bool
    future_fact_leakage: bool = False
    issues: list[str] = Field(default_factory=list, max_length=30)



class BeatScriptShot(BaseModel):
    """Writing-owned shot content before performance and camera direction."""

    model_config = ConfigDict(extra="forbid")

    local_index: int = Field(ge=1)
    scene_job: str = Field(min_length=1, max_length=120)
    change: str = Field(min_length=1, max_length=240)
    blocking: str = Field(min_length=1, max_length=300)
    characters: list[str] = Field(default_factory=list)
    location: str = ""
    source_quote: str = Field(min_length=1, max_length=500)
    event_ids: list[str] = Field(min_length=1)
    shot_intent: ShotIntent
    turns: list[ScriptTurn] = Field(min_length=1, max_length=6)



class RetentionBeatScript(BaseModel):
    model_config = ConfigDict(extra="forbid")

    beat_id: str = Field(pattern=r"^beat_\d{3}$")
    open_state: str = Field(min_length=1, max_length=300)
    close_state: str = Field(min_length=1, max_length=300)
    released_fact_ids: list[str] = Field(default_factory=list)
    shots: list[BeatScriptShot] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_local_indexes(self) -> "RetentionBeatScript":
        indexes = [shot.local_index for shot in self.shots]
        if indexes != list(range(1, len(self.shots) + 1)):
            raise ValueError("beat script local shot indexes must be consecutive")
        return self



class DirectedShot(BaseModel):
    """Direction-only contract for one contiguous range of immutable turns."""

    model_config = ConfigDict(extra="forbid")

    source_shot_index: int = Field(ge=1)
    turn_start: int = Field(ge=1)
    turn_end: int = Field(ge=1)
    shot_scale: str = "中近景"
    visual_prompt: str = Field(min_length=1, max_length=600)
    motion_prompt: str = Field(min_length=1, max_length=500)
    performance_plan: PerformancePlan
    camera_plan: CameraPlan
    visual_strategy: VisualStrategy = VisualStrategy.AUTO
    keyframe_reasons: list[str] = Field(default_factory=list)
    audio_plan: SceneAudioPlan
    script_open_state: HandoffState
    script_close_state: HandoffState

    @model_validator(mode="after")
    def validate_turn_range(self) -> "DirectedShot":
        if self.turn_end < self.turn_start:
            raise ValueError("directed shot turn_end must not precede turn_start")
        return self



class RetentionBeatDirection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    beat_id: str = Field(pattern=r"^beat_\d{3}$")
    shots: list[DirectedShot] = Field(min_length=1, max_length=18)



class ShotContentPatch(BaseModel):
    shot_index: int = Field(ge=1)
    turns: list[ScriptTurn] | None = None
    visual_prompt: str | None = Field(default=None, min_length=1)
    motion_prompt: str | None = Field(default=None, min_length=1)
    performance_plan: PerformancePlan | None = None
    change: str | None = Field(default=None, min_length=1, max_length=240)
    shot_intent: ShotIntent | None = None

    @model_validator(mode="after")
    def require_one_edit(self) -> "ShotContentPatch":
        if all(
            value is None
            for value in (
                self.turns,
                self.visual_prompt,
                self.motion_prompt,
                self.performance_plan,
                self.change,
                self.shot_intent,
            )
        ):
            raise ValueError("shot content patch requires at least one edited field")
        return self



class ScriptContentPatch(BaseModel):
    shots: list[ShotContentPatch] = Field(min_length=1)



class EpisodePlanningBundle(BaseModel):
    """All auditable writing-stage outputs required before media generation."""

    diagnosis: ChapterDiagnosis
    plan: EpisodePlan
    quality_report: ScriptQualityReport
    updated_series_state: SeriesState
    episode_contract: EpisodeContract | None = None



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


# Resolve the existing forward references within this model group.
ChapterEvent.model_rebuild()
ChapterDiagnosis.model_rebuild()
ScriptReviewIssue.model_rebuild()
ScriptQualityReport.model_rebuild()
GroundedStateFact.model_rebuild()
CharacterEpisodeState.model_rebuild()
RelationshipState.model_rebuild()
PropState.model_rebuild()
StoryLoop.model_rebuild()
EpisodeEndState.model_rebuild()
SeriesInformationState.model_rebuild()
SeriesState.model_rebuild()
ScriptTurnPatch.model_rebuild()
ScriptExpansion.model_rebuild()
StoryEngine.model_rebuild()
RelationshipPressureEdge.model_rebuild()
SetupPayoffObligation.model_rebuild()
ChapterProjection.model_rebuild()
SeriesDevelopmentPlan.model_rebuild()
SeriesDevelopmentReview.model_rebuild()
BeatScriptShot.model_rebuild()
RetentionBeatScript.model_rebuild()
DirectedShot.model_rebuild()
RetentionBeatDirection.model_rebuild()
ShotContentPatch.model_rebuild()
ScriptContentPatch.model_rebuild()
EpisodePlanningBundle.model_rebuild()
MediaPaths.model_rebuild()
VideoRecord.model_rebuild()
SubmissionManifest.model_rebuild()
