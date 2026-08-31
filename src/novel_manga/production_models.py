from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from .models import (
    CameraPlan,
    PerformancePlan,
    SceneAudioPlan,
    ShotIntent,
    TurnDelivery,
    VisualStrategy,
)


class AssetRecord(BaseModel):
    asset_id: str
    kind: str
    name: str
    version: str = "v001"
    approval_status: str = "approved"
    rights_status: str = "project-generated"
    identity_invariants: list[str] = Field(default_factory=list)
    state_variables: dict[str, str] = Field(default_factory=dict)
    reference_scope: dict[str, list[str]] = Field(default_factory=dict)
    spec_path: str
    primary_image: str
    secondary_image: str | None = None
    prompt_sha256: str


class SeriesAssetManifest(BaseModel):
    schema_version: int = 1
    style_fingerprint: str
    characters: list[AssetRecord]
    locations: list[AssetRecord]
    voice_assignments: dict[str, str]


class RuntimeUnit(BaseModel):
    unit_id: str
    episode_id: str
    scene_id: str
    shot_id: str
    shot_index: int = Field(ge=1)
    turn_index: int = Field(ge=1)
    role: str
    speaker_name: str
    speaking: bool
    delivery_mode: TurnDelivery = TurnDelivery.NARRATION
    reference_audio_required: bool = False
    text: str = Field(min_length=1, max_length=500)
    emotion: str
    source_quote: str = Field(min_length=1, max_length=500)
    character_asset_ids: list[str] = Field(default_factory=list)
    # A deliberately narrower cast used only by the direct character + empty
    # location H3 route.  character_asset_ids remains the complete story cast
    # needed by scene-aware keyframes and audit traces.
    direct_video_character_asset_ids: list[str] = Field(default_factory=list)
    location_asset_id: str
    location_name: str = ""
    voice: str
    visual_prompt: str
    motion_instruction: str = ""
    motion_prompt: str
    keyframe_prompt: str
    actor_description: str | None = None
    composition_prompt: str | None = None
    performance_plan: PerformancePlan | None = None
    camera_plan: CameraPlan | None = None
    visual_strategy: VisualStrategy = VisualStrategy.AUTO
    keyframe_reasons: list[str] = Field(default_factory=list)
    shot_intent: ShotIntent = Field(default_factory=ShotIntent)
    audio_plan: SceneAudioPlan = Field(default_factory=SceneAudioPlan)
    action_physics_plan: "ActionPhysicsPlan | None" = None
    audio_path: str
    keyframe_path: str
    raw_video_path: str
    segment_path: str
    subtitle_alignment: str = "pending"
    speech_start: float | None = None
    speech_end: float | None = None
    audio_seconds: float | None = None
    segment_seconds: float | None = None
    attempt: int = 0


class RuntimeShot(BaseModel):
    shot_id: str
    scene_id: str
    index: int = Field(ge=1)
    narrative_job: str
    location_asset_id: str
    source_quote: str
    shot_intent: ShotIntent = Field(default_factory=ShotIntent)
    unit_ids: list[str] = Field(min_length=1)


class RuntimeScene(BaseModel):
    scene_id: str
    index: int = Field(ge=1)
    location_asset_id: str
    narrative_job: str
    shot_ids: list[str] = Field(default_factory=list)
    spatial_contract: "SceneSpatialContract | None" = None


class SceneSpatialContract(BaseModel):
    location_version_id: str
    time_of_day: str = "unspecified"
    zones: dict[str, str] = Field(default_factory=dict)
    anchor_objects: list[str] = Field(default_factory=list)
    allowed_asset_ids: list[str] = Field(default_factory=list)
    lighting_source: str = "approved location reference"
    action_axis: str
    continuity_notes: list[str] = Field(default_factory=list)


class ActionPhysicsPlan(BaseModel):
    trigger: str
    preparation: str
    force: str
    contact: str
    reaction: str
    settling: str
    environment_feedback: list[str] = Field(default_factory=list, max_length=5)


class ReferenceScope(BaseModel):
    reference_id: str
    kind: str
    inherit: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)


class ShotContractBeat(BaseModel):
    start_seconds: float = Field(ge=0.0)
    end_seconds: float = Field(gt=0.0)
    actor_or_source: str
    trigger: str = ""
    action: str
    reaction: str = ""
    end_state: str = ""


class ShotContract(BaseModel):
    contract_version: str = "hell-grind-adapted-v1"
    narrative_goal: str
    duration_seconds: float = Field(gt=0.0, le=14.0)
    visible_asset_ids: list[str] = Field(default_factory=list)
    audible_roles: list[str] = Field(default_factory=list)
    reference_scopes: list[ReferenceScope] = Field(default_factory=list)
    open_state: str
    beat_timeline: list[ShotContractBeat] = Field(min_length=1, max_length=4)
    close_state: str
    camera_start: str
    camera_path: str
    camera_end: str
    continuity_in: str
    continuity_out: str
    must_hold: list[str] = Field(default_factory=list, max_length=8)
    changes_here: list[str] = Field(default_factory=list, max_length=5)
    must_not_appear: list[str] = Field(default_factory=list, max_length=5)
    risk_focus: list[str] = Field(default_factory=list, max_length=3)
    exact_dialogue: list[str] = Field(default_factory=list)
    external_audio_is_master: bool = True


class ProviderPromptAdapter(BaseModel):
    adapter_version: str = "runtime-compact-v1"
    provider: str
    image_model: str
    video_model: str
    contract_sha256: str
    reference_order: list[str] = Field(default_factory=list)
    image_prompt: str
    video_prompt: str


class ImagePromptContract(BaseModel):
    contract_version: str = "hell-grind-adapted-v1"
    purpose: str = "shot_start_keyframe"
    exact_subject_count: int = Field(ge=0, le=6)
    subject_asset_version_ids: list[str] = Field(default_factory=list)
    location_asset_version_id: str
    reference_scopes: list[ReferenceScope] = Field(default_factory=list)
    spatial_anchors: list[str] = Field(default_factory=list, max_length=4)
    action_moment: str
    composition: str
    perspective_focus: str
    lighting: str
    color_material: str
    risk_focus: list[str] = Field(default_factory=list, max_length=3)
    delivery: str = "9:16 portrait JPEG keyframe; no burned-in text"


class EpisodeSequenceContract(BaseModel):
    sequence_version: str = "hell-grind-adapted-v1"
    sequence_goal: str
    exact_generation_count: int = Field(ge=1)
    ordered_group_ids: list[str] = Field(min_length=1)
    narrative_progression: list[str] = Field(min_length=1)
    visual_motifs: list[str] = Field(default_factory=list, max_length=8)
    coverage_rhythm: list[str] = Field(min_length=1)
    camera_rhythm: list[str] = Field(min_length=1)
    lighting_continuity: str
    audio_continuity: str
    transition_rules: list[str] = Field(default_factory=list)
    final_landing: str


class RuntimeVisualGroup(BaseModel):
    """One continuous visual performance carrying one or more locked audio turns."""

    group_id: str
    scene_id: str
    shot_ids: list[str] = Field(min_length=1)
    unit_ids: list[str] = Field(min_length=1)
    location_asset_id: str
    character_asset_ids: list[str] = Field(default_factory=list)
    direct_video_character_asset_ids: list[str] = Field(default_factory=list)
    spatial_anchor: str
    combined_text: str = Field(min_length=1)
    keyframe_prompt: str = Field(min_length=1)
    motion_prompt: str = Field(min_length=1)
    audio_path: str
    video_audio_path: str
    keyframe_path: str
    raw_video_path: str
    segment_path: str
    audio_seconds: float | None = None
    segment_seconds: float | None = None
    speed_factor: float = Field(default=1.0, ge=1.0, le=1.12)
    visual_strategy: VisualStrategy = VisualStrategy.AUTO
    keyframe_reasons: list[str] = Field(default_factory=list)
    shot_contract: ShotContract | None = None
    image_contract: ImagePromptContract | None = None
    prompt_adapter: ProviderPromptAdapter | None = None


class ProductionPlan(BaseModel):
    schema_version: int = 1
    video_id: str
    source_title: str
    source_text_sha256: str
    style_fingerprint: str
    visual_style: str = ""
    palette: str = ""
    scenes: list[RuntimeScene] = Field(min_length=1)
    shots: list[RuntimeShot] = Field(min_length=1)
    units: list[RuntimeUnit] = Field(min_length=1)
    visual_groups: list[RuntimeVisualGroup] = Field(default_factory=list)
    sequence_contract: EpisodeSequenceContract | None = None

    @model_validator(mode="after")
    def validate_hierarchy(self) -> "ProductionPlan":
        scene_ids = {scene.scene_id for scene in self.scenes}
        shot_ids = {shot.shot_id for shot in self.shots}
        unit_ids = {unit.unit_id for unit in self.units}
        if len(scene_ids) != len(self.scenes) or len(shot_ids) != len(self.shots) or len(unit_ids) != len(self.units):
            raise ValueError("scene, shot, and unit identifiers must be unique")
        if {shot.scene_id for shot in self.shots} - scene_ids:
            raise ValueError("shots reference unknown scenes")
        if any(not scene.shot_ids for scene in self.scenes):
            raise ValueError("every runtime scene must reference at least one shot")
        if {unit.shot_id for unit in self.units} - shot_ids:
            raise ValueError("units reference unknown shots")
        referenced = {unit_id for shot in self.shots for unit_id in shot.unit_ids}
        if referenced != unit_ids:
            raise ValueError("every runtime unit must be referenced by exactly one shot set")
        for field in ("audio_path", "keyframe_path", "raw_video_path", "segment_path"):
            values = [getattr(unit, field) for unit in self.units]
            if len(values) != len(set(values)):
                raise ValueError(f"runtime units must not reuse complete {field} artifacts")
        for unit in self.units:
            if unit.speaking:
                if unit.role == "narrator" or not unit.character_asset_ids:
                    raise ValueError("visible speaking units require a locked non-narrator character asset")
                if unit.text not in unit.motion_prompt:
                    raise ValueError("visible speaking unit prompt must contain the exact locked dialogue")
                if unit.delivery_mode != TurnDelivery.VISIBLE_DIALOGUE:
                    raise ValueError("visible speaking units require visible_dialogue delivery")
            elif unit.role == "narrator":
                if unit.delivery_mode != TurnDelivery.NARRATION:
                    raise ValueError("narrator runtime units require narration delivery")
            elif unit.delivery_mode not in {
                TurnDelivery.OFFSCREEN_DIALOGUE,
                TurnDelivery.INNER_VOICE,
            }:
                raise ValueError("non-visible character audio requires offscreen or inner delivery")
        if self.visual_groups:
            grouped = [unit_id for group in self.visual_groups for unit_id in group.unit_ids]
            if len(grouped) != len(set(grouped)) or set(grouped) != unit_ids:
                raise ValueError("visual groups must partition runtime units exactly once")
            if {group.scene_id for group in self.visual_groups} - scene_ids:
                raise ValueError("visual groups reference unknown scenes")
            for field in ("audio_path", "keyframe_path", "raw_video_path", "segment_path"):
                values = [getattr(group, field) for group in self.visual_groups]
                if len(values) != len(set(values)):
                    raise ValueError(f"visual groups must not reuse {field} artifacts")
        return self
