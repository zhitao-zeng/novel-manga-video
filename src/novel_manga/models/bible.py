from __future__ import annotations
import re
from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator



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


# Resolve the existing forward references within this model group.
Character.model_rebuild()
StoryBible.model_rebuild()
