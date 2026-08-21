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


class TurnDerivation(StrEnum):
    """How a turn's text relates to the chapter text it is grounded in.

    ``VERBATIM`` copies a quoted line out of the chapter.  ``DERIVED`` stages a
    narrated passage as dialogue, action or reaction; it stays bound to the
    narration it came from and may not introduce facts that narration does not
    already carry.  Without this distinction the only legal move for a narrated
    passage is to become explanatory narration, which the short-drama profile
    then budgets away, and the chapter's causal tissue disappears.
    """

    VERBATIM = "verbatim"
    DERIVED = "derived"


class VisualStrategy(StrEnum):
    """How a shot obtains its initial visual conditioning."""

    AUTO = "auto"
    DIRECT_ASSETS = "direct-assets"
    SCENE_ONLY = "scene-only"
    STORY_KEYFRAME = "story-keyframe"


class SpeechStrategy(StrEnum):
    """Whether spoken words are locked before video generation."""

    LOCKED = "locked"
    NATIVE = "native"
    ADAPTIVE = "adaptive"


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
    visual_archetype: str = ""
    face_anchors: list[str] = Field(default_factory=list)
    silhouette: str = ""
    hair: str = ""
    palette: str = ""
    base_costume: str = ""
    episode_costumes: list[str] = Field(default_factory=list)
    signature_prop: str = ""
    expression_profile: str = ""
    motion_signature: str = ""
    voice_profile_id: str = ""


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
    policy_revision: str = "novel-manga-script-v6-showrunner"
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
    derived_turn_count: int = Field(default=0, ge=0)
    derived_char_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
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


class ScriptTurn(BaseModel):
    """One semantic utterance; subtitle pagination is deliberately separate."""

    role: str = "narrator"
    speaker_name: str = "旁白"
    text: str = Field(min_length=1, max_length=500)
    speaking: bool = False
    delivery_mode: TurnDelivery | None = None
    emotion: str = "克制自然"
    source_quote: str = Field(default="", max_length=500)
    derivation: TurnDerivation = TurnDerivation.VERBATIM

    @model_validator(mode="after")
    def validate_speaker(self) -> "ScriptTurn":
        if "derivation" not in self.model_fields_set and self.role == "narrator":
            # Narration is a retelling by construction; only a character line
            # can meaningfully claim to be a verbatim copy of a quoted line.
            self.derivation = TurnDerivation.DERIVED
        if self.delivery_mode is None:
            if self.role == "narrator":
                self.delivery_mode = TurnDelivery.NARRATION
            elif self.speaking:
                self.delivery_mode = TurnDelivery.VISIBLE_DIALOGUE
            else:
                self.delivery_mode = TurnDelivery.OFFSCREEN_DIALOGUE
        # speaking and delivery_mode encode the same fact twice, and planners
        # routinely disagree with themselves across the two.  delivery_mode is
        # the richer field, so let it decide and reconcile the boolean, rather
        # than spending a revision round on a contradiction that carries no
        # information.
        if self.delivery_mode is not None:
            self.speaking = self.delivery_mode == TurnDelivery.VISIBLE_DIALOGUE
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


class AudioBeat(BaseModel):
    """A relative, trigger-bound sound event inside one shot."""

    position_ratio: float = Field(ge=0.0, le=1.0)
    cue_type: str = Field(
        pattern=(
            r"^(silence|ambience|impact|music_rise|music_cut|bass_drop|"
            r"heartbeat|sfx|duck|release)$"
        )
    )
    cue: str = Field(min_length=1, max_length=160)
    trigger: str = Field(min_length=1, max_length=160)
    retention_beat_id: str = ""


class SceneAudioPlan(BaseModel):
    """Editorial sound intent; TTS remains limited to actual speech."""

    speech_strategy: SpeechStrategy = SpeechStrategy.LOCKED
    voice_reference_id: str = ""
    delivery_intent: str = "克制自然"
    pace: str = "自然"
    energy: float = Field(default=0.5, ge=0.0, le=1.0)
    pauses: list[str] = Field(default_factory=list)
    music_cue: str = ""
    ambience: str = ""
    sfx_events: list[str] = Field(default_factory=list)
    audio_beats: list[AudioBeat] = Field(default_factory=list, max_length=8)
    ducking: bool = True

    @model_validator(mode="after")
    def validate_audio_beat_order(self) -> "SceneAudioPlan":
        positions = [beat.position_ratio for beat in self.audio_beats]
        if positions != sorted(positions):
            raise ValueError("audio beats must be ordered by position_ratio")
        return self


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


class ShotIntent(BaseModel):
    dramatic_function: str = Field(
        default="advance",
        pattern=(
            r"^(establish|advance|pressure|withhold|reveal|payoff|reaction|"
            r"transition|cliffhanger)$"
        ),
    )
    power_relation: str = "未明确"
    emotion_target: str = "保持关注"
    information_fact_ids: list[str] = Field(default_factory=list)
    viewer_focus: str = "当前主要动作与反应"
    retention_beat_id: str = ""


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
    visual_strategy: VisualStrategy = VisualStrategy.AUTO
    keyframe_reasons: list[str] = Field(default_factory=list)
    shot_intent: ShotIntent = Field(default_factory=ShotIntent)
    audio_plan: SceneAudioPlan = Field(default_factory=SceneAudioPlan)


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
