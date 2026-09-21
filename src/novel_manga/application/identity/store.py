"""Read chapter identity inputs once per operation and retain the existing cache keys."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from novel_manga.util import read_json
from novel_manga.story.catalog import IdentityCatalog
from novel_manga.story.source_identity import POLICY, usable_reading

def read(path, default=None):
    return read_json(Path(path), default)


def data_files(novel):
    """Every file that can change how a chapter binds - the cache key is only as good as this list.

    reading_cast.json belongs here because map_source_reading() consults its aliases: without it, adding
    the alias that proves 蝙蝠侠 and 布鲁斯 are one man leaves the chapter's saved UNKNOWN binding in place
    and the plan keeps failing at a defect that was already fixed.  Re-binding is cheap: resolve_chapter's
    second early return reuses the saved source_actors whenever the segments are unchanged, so a new alias
    costs no model call - it only redoes the binding, which is exactly what changed.
    """
    novel = Path(novel).resolve()
    return [novel / name for name in ['story_bible.json', 'bible_aliases.json', 'entity_index.json',
            'reading_cast.json',
            'series_assets/phases.json', 'entity/entities.json', 'entity/claims.json', 'entity/types.json']]


def data_signature(novel):
    return {str(p): [p.stat().st_mtime_ns, p.stat().st_size] if p.is_file() else None for p in data_files(novel)}


def chapter_inputs(directory):
    directory = Path(directory).resolve()
    return {'data': data_signature(directory.parent), 'segments': read(directory / 'segments.json', [])}



@dataclass
class BookIdentityData:
    catalog: IdentityCatalog
    index: dict
    types: dict


def load_book(novel: Path) -> BookIdentityData:
    novel = Path(novel)
    index = read(novel / 'entity_index.json', {})
    catalog = IdentityCatalog(read(novel / 'story_bible.json', {}),
        read(novel / 'entity/entities.json', []), read(novel / 'bible_aliases.json', {}), index,
        read(novel / 'entity/claims.json', []), read(novel / 'series_assets/phases.json', {}))
    return BookIdentityData(catalog, index, read(novel / 'entity/types.json', {}))


def load_catalog(novel: Path) -> IdentityCatalog:
    return load_book(novel).catalog


@dataclass
class ChapterIdentityData:
    directory: Path
    book: BookIdentityData
    expected: dict
    saved: dict

    @property
    def catalog(self):
        return self.book.catalog

    @property
    def segments(self):
        return self.expected['segments']

    @property
    def context(self):
        saved = self.saved
        return saved if (saved.get('policy') == POLICY and usable_reading(saved)
                         and saved.get('inputs') == self.expected) else {}


def load_chapter(directory: Path) -> ChapterIdentityData:
    directory = Path(directory).resolve()
    return ChapterIdentityData(directory, load_book(directory.parent), chapter_inputs(directory),
                               read(directory / 'identity_context.json', {}))


def current_context(directory, *, data: ChapterIdentityData | None = None):
    if data is not None:
        return data.context
    result = read(Path(directory) / 'identity_context.json', {})
    return result if result.get('policy') == POLICY and usable_reading(result) and result.get('inputs') == chapter_inputs(Path(directory)) else {}
