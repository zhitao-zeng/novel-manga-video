"""In-memory evidence for a single clip. No new persisted review format."""
from dataclasses import dataclass
from novel_manga.models import Character


@dataclass
class ClipEvidence:
    by_name: dict[str, Character]
    cast: list[str]
    extras: list[str]
    listeners: list[str]
    offscreen: list[str]
    background: list[str]
    legend: list[str]
    segments: dict[str, str]
    snapshot: str
    source_contract: str
    world: str
    identity: str
    chat_screen: dict
