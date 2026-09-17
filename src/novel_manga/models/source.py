from __future__ import annotations
import re
from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator



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


# Resolve the existing forward references within this model group.
Episode.model_rebuild()
NovelDocument.model_rebuild()
