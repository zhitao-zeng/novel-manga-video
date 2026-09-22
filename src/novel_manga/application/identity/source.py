"""Source-only adapter for existing repair callers; scene rules live in the shared library."""
import novel_manga.episodes as ep_names
from pathlib import Path
import json
from novel_manga.story.scene import resolve_identities
from novel_manga.application.identity.scene import load_scene_context


def resolve_script(script, novel: Path, segments: list[dict], *, chapter=None):
    chapter = chapter if chapter is not None else script.get('episode_index')
    result = resolve_identities(script, load_scene_context(novel, chapter, segments=segments))
    script.clear()
    script.update(result.script)
    return result.changes


def resolved_changes(directory: Path):
    script = json.loads((directory / 'chapter_script.json').read_text())
    segments = json.loads((directory / 'segments.json').read_text())
    return resolve_script(script, directory.parent, segments, chapter=ep_names.chapter_of(directory.name))
