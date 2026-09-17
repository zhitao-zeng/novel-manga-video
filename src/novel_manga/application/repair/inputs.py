"""Episode evidence read once for a stable repair scan; callers reload after writes."""
from dataclasses import dataclass, field
from pathlib import Path
from novel_manga.application.identity.store import ChapterIdentityData, load_chapter
from novel_manga.util import read_json


@dataclass
class RepairInputs:
    directory: Path
    identity: ChapterIdentityData
    script: dict
    notes: dict
    speaker_facts: list
    asset_stats: dict = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Path):
        return cls(directory, load_chapter(directory), read_json(directory / 'chapter_script.json', {}),
                   read_json(directory / 'review_feedback.json', {}),
                   read_json(directory / 'source_speaker_contract.json', []))

    def stat(self, path: Path):
        key = str(path)
        if key not in self.asset_stats:
            self.asset_stats[key] = [path.stat().st_mtime_ns, path.stat().st_size] if path.is_file() else None
        return self.asset_stats[key]
