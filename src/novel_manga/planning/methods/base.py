"""Local creative methods describe decisions; they never call models or read files."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StoryMethod:
    key: str
    name: str
    author: str
    sources: tuple[str, ...]
    strategy: str
    episode_fields: tuple[tuple[str, str], ...]
    beat_fields: tuple[tuple[str, str], ...]
    drafting: str
    screenwriting: str = ""
    version: str = "2"

    def describe(self) -> dict:
        return {"id": self.key, "name": self.name, "version": self.version,
                "author": self.author, "sources": list(self.sources),
                "writing_role": "method" if self.screenwriting else "shared",
                "episode_decisions": dict(self.episode_fields), "beat_decisions": dict(self.beat_fields)}
