from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class AssetRecord(BaseModel):
    asset_id: str
    kind: str
    name: str
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
    text: str = Field(min_length=1, max_length=500)
    emotion: str
    source_quote: str = Field(min_length=1, max_length=500)
    character_asset_ids: list[str] = Field(default_factory=list)
    location_asset_id: str
    voice: str
    visual_prompt: str
    motion_prompt: str
    keyframe_prompt: str
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
    unit_ids: list[str] = Field(min_length=1)


class RuntimeScene(BaseModel):
    scene_id: str
    index: int = Field(ge=1)
    location_asset_id: str
    narrative_job: str
    shot_ids: list[str] = Field(default_factory=list)


class ProductionPlan(BaseModel):
    schema_version: int = 1
    video_id: str
    source_title: str
    source_text_sha256: str
    style_fingerprint: str
    scenes: list[RuntimeScene] = Field(min_length=1)
    shots: list[RuntimeShot] = Field(min_length=1)
    units: list[RuntimeUnit] = Field(min_length=1)

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
            elif unit.role != "narrator":
                raise ValueError("non-speaking audio turns must use the narrator role")
        return self
