from __future__ import annotations
import re
from enum import StrEnum
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, model_validator



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
    props: list[AssetRecord] = []
    voice_assignments: dict[str, str]


# Resolve the existing forward references within this model group.
AssetRecord.model_rebuild()
SeriesAssetManifest.model_rebuild()
