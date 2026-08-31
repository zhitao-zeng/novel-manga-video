from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    TITLE_CARD = "title_card"
    SILENT_ACTION = "silent_action"


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
    ABRIDGED = "abridged"
    DERIVED = "derived"


class TurnDevice(StrEnum):
    LISTENER_QA = "listener_qa"
    CROWD_PROXY = "crowd_proxy"
    HALF_LINE = "half_line"
    EVIDENCE_OBJECT = "evidence_object"
    SPATIAL = "spatial"
    CONSEQUENCE = "consequence"
    INNER_VOICE = "inner_voice"
    NARRATION = "narration"


class EpisodeMode(StrEnum):
    CHOICE = "choice_episode"
    PRESSURE = "pressure_episode"


class MotionActionType(StrEnum):
    """The visible dramatic job performed by one causal motion beat."""

    CHOOSE = "choose"
    REFUSE = "refuse"
    CONFRONT = "confront"
    ASK = "ask"
    MOVE = "move"
    REVEAL = "reveal"
    REACT = "react"
    WAIT = "wait"
    PRESS = "press"


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
    device: TurnDevice | None = None
    serves: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_narrator_role(cls, data):
        if isinstance(data, dict):
            role = str(data.get("role", "")).strip()
            speaker = str(data.get("speaker_name", "")).strip()
            delivery = str(data.get("delivery_mode", "")).strip()
            if delivery == TurnDelivery.SILENT_ACTION and role in {
                "",
                "narrator",
                "旁白",
            }:
                return {**data, "role": "action", "speaker_name": ""}
            if (
                role in {"", "narrator", "旁白"}
                and speaker not in {"", "narrator", "旁白"}
                and delivery
                in {
                    TurnDelivery.VISIBLE_DIALOGUE,
                    TurnDelivery.OFFSCREEN_DIALOGUE,
                    "visible_dialogue",
                    "offscreen_dialogue",
                }
            ):
                return {**data, "role": speaker}
            if role == "旁白":
                return {**data, "role": "narrator"}
        return data

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
        if (
            self.delivery_mode != TurnDelivery.SILENT_ACTION
            and self.role != "narrator"
            and not self.speaker_name.strip()
        ):
            raise ValueError("character voice turns require speaker_name")
        if self.role == "narrator" and self.delivery_mode not in {
            TurnDelivery.NARRATION,
            TurnDelivery.TITLE_CARD,
        }:
            raise ValueError("narrator turns must use narration or title_card delivery")
        if self.speaking and self.delivery_mode != TurnDelivery.VISIBLE_DIALOGUE:
            raise ValueError("visible speaking turns must use visible_dialogue delivery")
        if not self.speaking and self.role != "narrator" and self.delivery_mode not in {
            TurnDelivery.OFFSCREEN_DIALOGUE,
            TurnDelivery.INNER_VOICE,
            TurnDelivery.SILENT_ACTION,
        }:
            raise ValueError(
                "non-visible character turns must use offscreen_dialogue, inner_voice, or silent_action"
            )
        invalid_serves = [
            value
            for value in self.serves
            if not re.fullmatch(r"(?:event|fact)_\d{3}", value)
        ]
        if invalid_serves:
            raise ValueError(f"invalid serves ids: {invalid_serves}")
        if (
            self.device == TurnDevice.NARRATION
            and self.delivery_mode != TurnDelivery.NARRATION
        ):
            raise ValueError("narration device requires narration delivery")
        if (
            self.device == TurnDevice.INNER_VOICE
            and self.delivery_mode != TurnDelivery.INNER_VOICE
        ):
            raise ValueError("inner_voice device requires inner_voice delivery")
        return self


class ScriptTurnPatch(BaseModel):
    shot_index: int = Field(ge=1)
    turns: list[ScriptTurn] = Field(min_length=1)


class ScriptExpansion(BaseModel):
    shots: list[ScriptTurnPatch] = Field(min_length=1)


class MotionBeat(BaseModel):
    """One causally ordered performance beat inside a continuous shot."""

    phase: str = Field(pattern=r"^(opening|development|resolution)$")
    seconds: float | None = Field(default=None, gt=0.0, le=14.0)
    actor: str = Field(default="", max_length=80)
    target: str = Field(default="", max_length=120)
    action_type: MotionActionType = MotionActionType.REACT
    trigger: str = ""
    action: str = Field(min_length=1, max_length=240)
    reaction: str = Field(default="", max_length=180)
    expression_transition: str = Field(default="", max_length=120)
    end_state: str = Field(default="", max_length=180)


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
    """Editorial sound intent for video-model native dialogue."""

    speech_strategy: SpeechStrategy = SpeechStrategy.NATIVE
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
    episode_mode: EpisodeMode = EpisodeMode.PRESSURE
    protagonist_choice: str = Field(default="", max_length=240)
    choice_source_quote: str = Field(default="", max_length=500)
    cost_paid: str = Field(default="", max_length=240)
    cost_source_quote: str = Field(default="", max_length=500)
    opposition: "EpisodeOpposition | None" = None


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

    @model_validator(mode="before")
    @classmethod
    def normalize_retention_function_alias(cls, data):
        if not isinstance(data, dict):
            return data
        aliases = {
            "hook": "establish",
            "question": "withhold",
            "escalation": "pressure",
            "reversal": "reveal",
            "climax": "payoff",
        }
        value = str(data.get("dramatic_function", "")).strip()
        if value in aliases:
            return {**data, "dramatic_function": aliases[value]}
        return data


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


class HandoffState(BaseModel):
    knowledge: dict[str, str] = Field(default_factory=dict)
    power: dict[str, str] = Field(default_factory=dict)
    relationship: dict[str, str] = Field(default_factory=dict)
    physical: dict[str, str] = Field(default_factory=dict)
    ongoing_action: str = "none"


class Shot(BaseModel):
    index: int = Field(ge=1)
    narration: str = Field(min_length=1, max_length=80)
    subtitle: str = Field(min_length=1, max_length=80)
    visual_prompt: str
    motion_prompt: str
    characters: list[str] = Field(default_factory=list)
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
