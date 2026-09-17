from __future__ import annotations
import re
from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator



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



class HandoffState(BaseModel):
    knowledge: dict[str, str] = Field(default_factory=dict)
    power: dict[str, str] = Field(default_factory=dict)
    relationship: dict[str, str] = Field(default_factory=dict)
    physical: dict[str, str] = Field(default_factory=dict)
    ongoing_action: str = "none"


# Resolve the existing forward references within this model group.
MotionBeat.model_rebuild()
PerformancePlan.model_rebuild()
CameraBeat.model_rebuild()
CameraPlan.model_rebuild()
AudioBeat.model_rebuild()
SceneAudioPlan.model_rebuild()
ShotIntent.model_rebuild()
HandoffState.model_rebuild()
