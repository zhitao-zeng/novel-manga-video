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


class TurnDelivery(StrEnum):
    """How an utterance is heard and whether it should drive visible lips."""

    NARRATION = "narration"
    VISIBLE_DIALOGUE = "visible_dialogue"
    OFFSCREEN_DIALOGUE = "offscreen_dialogue"
    INNER_VOICE = "inner_voice"


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


class AdaptationLedgerItem(BaseModel):
    event_id: str = Field(pattern=r"^event_\d{3}$")
    disposition: str = Field(
        pattern=r"^(preserved|compressed|merged|externalized|removed)$"
    )
    shot_indexes: list[int] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=240)


class ScriptReviewIssue(BaseModel):
    code: str
    severity: str = Field(pattern=r"^(blocking|warning)$")
    message: str = Field(min_length=1, max_length=500)
    shot_indexes: list[int] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)


class ScriptQualityReport(BaseModel):
    policy_revision: str = "novel-manga-script-v3"
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


class SeriesState(BaseModel):
    """Compact dynamic memory carried from episode N to episode N+1."""

    schema_version: int = 1
    current_episode: int = Field(ge=0)
    timeline: list[GroundedStateFact] = Field(default_factory=list)
    characters: list[CharacterEpisodeState] = Field(default_factory=list)
    relationships: list[RelationshipState] = Field(default_factory=list)
    props: list[PropState] = Field(default_factory=list)
    open_loops: list[StoryLoop] = Field(default_factory=list)
    resolved_loops: list[StoryLoop] = Field(default_factory=list)
    potential_foreshadowing: list[GroundedStateFact] = Field(default_factory=list)
    previous_episode_end: EpisodeEndState | None = None


class ScriptTurn(BaseModel):
    """One semantic utterance; subtitle pagination is deliberately separate."""

    role: str = "narrator"
    speaker_name: str = "旁白"
    text: str = Field(min_length=1, max_length=500)
    speaking: bool = False
    delivery_mode: TurnDelivery | None = None
    emotion: str = "克制自然"
    source_quote: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_speaker(self) -> "ScriptTurn":
        if self.delivery_mode is None:
            if self.role == "narrator":
                self.delivery_mode = TurnDelivery.NARRATION
            elif self.speaking:
                self.delivery_mode = TurnDelivery.VISIBLE_DIALOGUE
            else:
                self.delivery_mode = TurnDelivery.OFFSCREEN_DIALOGUE
        if self.role == "narrator" and self.speaking:
            raise ValueError("narrator turns cannot be visible speaking turns")
        if self.role != "narrator" and not self.speaker_name.strip():
            raise ValueError("character voice turns require speaker_name")
        if self.role == "narrator" and self.delivery_mode != TurnDelivery.NARRATION:
            raise ValueError("narrator turns must use narration delivery")
        if self.speaking and self.delivery_mode != TurnDelivery.VISIBLE_DIALOGUE:
            raise ValueError("visible speaking turns must use visible_dialogue delivery")
        if not self.speaking and self.role != "narrator" and self.delivery_mode not in {
            TurnDelivery.OFFSCREEN_DIALOGUE,
            TurnDelivery.INNER_VOICE,
        }:
            raise ValueError("non-visible character turns must use offscreen_dialogue or inner_voice")
        return self


class ScriptTurnPatch(BaseModel):
    shot_index: int = Field(ge=1)
    turns: list[ScriptTurn] = Field(min_length=1)


class ScriptExpansion(BaseModel):
    shots: list[ScriptTurnPatch] = Field(min_length=1)


class MotionBeat(BaseModel):
    """One causally ordered performance beat inside a continuous shot."""

    phase: str = Field(pattern=r"^(opening|development|resolution)$")
    trigger: str = ""
    action: str = Field(min_length=1, max_length=240)
    reaction: str = Field(default="", max_length=180)
    expression_transition: str = Field(default="", max_length=120)


class PerformancePlan(BaseModel):
    objective: str = Field(min_length=1, max_length=180)
    start_state: str = Field(min_length=1, max_length=240)
    motion_beats: list[MotionBeat] = Field(min_length=1, max_length=4)
    end_state: str = Field(min_length=1, max_length=180)


class CameraBeat(BaseModel):
    phase: str = Field(pattern=r"^(opening|development|resolution)$")
    trajectory: str = Field(min_length=1, max_length=200)
    framing: str = Field(min_length=1, max_length=160)
    parallax: str = Field(min_length=1, max_length=180)


class CameraPlan(BaseModel):
    mode: str = Field(
        default="locked",
        pattern=r"^(locked|motivated_subtle|motivated_emphasis)$",
    )
    motivation: str = Field(default="人物表演承担画面动态", max_length=180)
    action_axis: str = Field(default="沿首次建立的行动轴同侧取景", max_length=180)
    screen_direction: str = Field(default="保持人物左右位置、视线和运动方向连续", max_length=180)
    start_position: str = Field(min_length=1, max_length=180)
    camera_beats: list[CameraBeat] = Field(min_length=1, max_length=3)
    end_position: str = Field(min_length=1, max_length=180)

    @model_validator(mode="after")
    def validate_motivated_camera(self) -> "CameraPlan":
        if self.mode != "locked" and self.motivation == "人物表演承担画面动态":
            raise ValueError("moving camera plans require an explicit narrative motivation")
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
    event_ids: list[str] = Field(default_factory=list)
    shot_scale: str = "中近景"
    turns: list[ScriptTurn] = Field(default_factory=list)
    performance_plan: PerformancePlan | None = None
    camera_plan: CameraPlan | None = None


class EpisodePlan(BaseModel):
    video_title: str
    hook: str
    summary: str
    shots: list[Shot] = Field(min_length=1)
    next_preview: str = "敬请期待下一集"
    adaptation_ledger: list[AdaptationLedgerItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shot_order(self) -> "EpisodePlan":
        expected = list(range(1, len(self.shots) + 1))
        actual = [shot.index for shot in self.shots]
        if actual != expected:
            raise ValueError(f"shot indexes must be consecutive: {actual}")
        return self


class EpisodePlanningBundle(BaseModel):
    """All auditable writing-stage outputs required before media generation."""

    diagnosis: ChapterDiagnosis
    plan: EpisodePlan
    quality_report: ScriptQualityReport
    updated_series_state: SeriesState


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
