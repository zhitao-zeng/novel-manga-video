"""Resolve one isolated Agent run without changing the source book."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

from novel_manga.application.configuration import project_root as configured_root
from novel_manga.episodes import episode_name, parse_episode
from novel_manga.util import read_json


@dataclass(frozen=True)
class EpisodeContext:
    project_root: Path
    source_novel_dir: Path
    source_episode_dir: Path
    run_dir: Path
    novel_dir: Path
    episode_dir: Path
    work_dir: Path
    input_mode: str
    target_seconds: tuple[float, float] | None
    clip_seconds_max: float = 15

    @property
    def asset_snapshot_dir(self) -> Path:
        return self.run_dir.parent / 'assets-snapshot'


def _target(value):
    if value is None:
        return None
    values = (value, value) if isinstance(value, (int, float)) else tuple(value)
    if (len(values) != 2 or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                              or v <= 0 for v in values) or values[0] > values[1]):
        raise ValueError('target_seconds requires a positive minimum/maximum pair')
    return tuple(float(v) for v in values)


def create_context(novel_dir, episode, *, output_root=None, input_mode='auto', target_seconds=None):
    source = Path(novel_dir).expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f'novel directory is missing: {source}')
    if input_mode not in {'auto', 'source', 'script'}:
        raise ValueError('input_mode must be auto, source or script')
    metadata = read_json(source / 'novel.json', {})
    book_id = str(metadata.get('novel_id') or source.name)
    name = str(episode)
    if name.isdigit():
        name = episode_name(book_id, int(name))
    candidate = Path(name)
    source_episode = candidate.resolve() if candidate.is_absolute() else source / candidate
    if source_episode.parent != source:
        raise ValueError('episode must belong to the selected novel directory')
    if parse_episode(source_episode.name) is None:
        raise ValueError(f'not an episode directory name: {source_episode.name}')
    script = read_json(source_episode / 'chapter_script.json', None)
    mode = ('script' if script is not None else 'source') if input_mode == 'auto' else input_mode
    if mode == 'script' and script is None:
        raise ValueError('script mode requires an existing chapter_script.json')
    target = _target(target_seconds)
    plan = read_json(source_episode / 'clip_plan.json', {}) if mode == 'script' else {}
    profile = read_json(source / 'profile.json', {})
    if target is None:
        total = sum(float(c.get('request_seconds') or 0) for c in plan.get('clips', []))
        budget = profile.get('target_seconds') or profile.get('episode_seconds')
        target = (total, total) if total > 0 else _target(budget) if budget else None
        if mode == 'source':
            target = (120.0, 150.0)
    root = configured_root()
    output = Path(output_root).expanduser().resolve() if output_root else root / 'outputs/episode-agent'
    if output.is_relative_to(source):
        raise ValueError('Agent output_root must be outside the source novel directory')
    run = output / source.name / f'{source_episode.name}-{mode}'
    book = run / source.name
    return EpisodeContext(root, source, source_episode, run, book,
                          book / source_episode.name, run / 'sandbox', mode, target)
