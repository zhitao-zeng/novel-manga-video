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



class Prop(BaseModel):
    """A plot object with its own card: named, recurring or plot-driving (易手/认主/被毁/开启).

    Wearables with their own identity (armor) are props too - the wearer becomes a phase whose
    card is drawn from this prop's card, so the armor has exactly one source of truth.
    """
    name: str
    category: str = "其他"          # 武器 | 信物 | 法器 | 工具 | 其他
    appearance: str = ""            # 稳定特征，不写当下状态（同人物外貌规则）
    material: str = ""
    owner: str = ""                 # 初登场持有者；易手戏由分镜表达，不跟卡走
    first_chapter: int = 0
    quote: str = ""                 # 原文连续复制的证据
    closeup: bool = False           # 需要特写 → 建 detail.jpeg 材质特写
    wearable: bool = False          # 可穿戴 → phase 卡以它的卡为参考图生成



class StoryBible(BaseModel):
    novel_title: str
    genre: str
    visual_style: str
    palette: str
    typography: str = "粗体无衬线中文字体，白字黑描边"
    characters: list[Character] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    props: list[Prop] = Field(default_factory=list)
    continuity_rules: list[str] = Field(default_factory=list)
    style_fingerprint: str


# Resolve the existing forward references within this model group.
Character.model_rebuild()
Prop.model_rebuild()
StoryBible.model_rebuild()
