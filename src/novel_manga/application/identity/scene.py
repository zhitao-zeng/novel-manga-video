"""Load the existing chapter evidence for the pure scene resolver. No model requests."""
from pathlib import Path
import re
from novel_manga.story.scene import SceneContext, ResolvedScene, resolve_scene
from novel_manga.story.identity import unique_forms
from novel_manga.application.identity.store import read, load_chapter, load_book
from novel_manga.application.identity.context import effective_aliases, typed_entities


def load_scene_context(novel: Path, chapter=None, *, segments=None, compilation=None, identity_data=None):
    novel = Path(novel)
    directory = novel / f'{novel.name}_{chapter}' if chapter is not None else None
    path = directory / 'segments.json' if directory else None
    source_available = segments is not None or bool(path and path.is_file())
    data = identity_data if identity_data is not None else load_chapter(directory) if directory else None
    book = data.book if data is not None else load_book(novel)
    segments = segments if segments is not None else data.segments if data is not None else []
    identity = data.context if data is not None else {}
    from novel_manga.story.identity import resolved_aliases
    aliases = resolved_aliases(identity, book.catalog.aliases)
    index = book.index
    forms, generic = {}, {}
    for row in index.get('characters', []):
        name = row['name']
        forms[name] = sorted({name, *(f for f in (row.get('forms') or {}) if len(f) >= 2 or f == name)}, key=len, reverse=True)
        generic[name] = bool(row.get('generic', len(name) < 3))
    bible_path = novel / 'story_bible.json'
    bible_names = {c['name'] for c in book.catalog.bible.get('characters', [])} if bible_path.is_file() else set(forms)
    all_forms = {n: {f for f in [n, *own, *(a for a, target in aliases.items() if target == n)] if len(f) >= 2}
                 for n, own in forms.items() if n in bible_names}
    usable = unique_forms(all_forms)
    eligible = {n: own for n, own in usable.items() if len(n) >= 2 and not generic[n]}
    return SceneContext(segments=segments, source_available=source_available, bible_names=bible_names,
                        entity_forms=forms, mention_forms=eligible, aliases=aliases, identity=identity,
                        types=typed_entities(novel, identity, data=data),
                        speaker_facts=read(directory / 'source_speaker_contract.json', []) if directory else [],
                        compilation=compilation or {})


def prepare_scene(script: dict | ResolvedScene, directory: Path, *, compilation=None, identity_data=None) -> ResolvedScene:
    match = re.search(r'_(\d+)$', directory.name)
    chapter = int(match[1]) if match else None
    context = load_scene_context(directory.parent, chapter, compilation=compilation, identity_data=identity_data)
    return resolve_scene(script, context)
