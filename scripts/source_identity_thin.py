"""Resolve a shortened character name only when this passage identifies its owner."""
from __future__ import annotations

import json
from pathlib import Path
import re


def resolve_script(script: dict, novel: Path, segments: list[dict], *, chapter: int | None = None) -> dict[int, dict[str, str]]:
    from plan_chapter_thin import load_entity_index, ENTITY_FORMS, mentioned_characters
    from story_identity import current_context, effective_aliases
    chapter = chapter if chapter is not None else script.get('episode_index')
    scoped = current_context(novel / f'{novel.name}_{chapter}') if chapter is not None else {}
    scoped_aliases = effective_aliases(novel, chapter, scoped) if scoped else {}
    load_entity_index(novel, chapter)
    if not ENTITY_FORMS and not scoped:
        return {}
    bible_path = novel / 'story_bible.json'
    bible_names = {c['name'] for c in json.loads(bible_path.read_text()).get('characters', [])} if bible_path.is_file() else set(ENTITY_FORMS)
    names = [n for n in ENTITY_FORMS if n in bible_names]
    by_segment = {str(s.get('segment_id')): s.get('text', '') for s in segments}
    chapter = '\n'.join(by_segment.values())
    changed = {}
    for index, shot in enumerate(script.get('shots', []), 1):
        source = str(shot.get('source_quote') or '') + '\n' + by_segment.get(str(shot.get('segment_id')), '')
        listed = set(shot.get('characters', [])) | set(shot.get('in_frame') or [])
        listed.update(t.get('speaker_name', '') for t in shot.get('turns', []))
        mapping = {}
        for old in listed:
            if not old:
                continue
            if scoped:
                target = scoped_aliases.get(old)
                if target and target in bible_names:
                    mapping[old] = target
                continue
            anchors = {old}
            alternatives = {name for name in names if name != old and anchors.intersection(ENTITY_FORMS[name])}
            if not alternatives:
                continue
            context = source if any(a in source for a in anchors) else chapter
            mentioned = set(mentioned_characters(context, names))
            candidates = {name for name in alternatives & mentioned
                          if any(anchor in form and len(form) > len(anchor) and form in context
                                 for form in ENTITY_FORMS[name] for anchor in anchors)}
            # A separate mention of the shorter canonical name remains ambiguous.
            if old not in mentioned and len(candidates) == 1:
                mapping[old] = next(iter(candidates))
        if not mapping:
            continue
        pattern = re.compile('|'.join(re.escape(s) for s in sorted(mapping, key=len, reverse=True)))
        for key in ('characters', 'in_frame', 'listeners'):
            if key in shot:
                shot[key] = list(dict.fromkeys(mapping.get(n, n) for n in shot.get(key) or []))
        for turn in shot.get('turns', []):
            turn['speaker_name'] = mapping.get(turn.get('speaker_name'), turn.get('speaker_name', ''))
        for action in shot.get('actions', []):
            for key in ('actor', 'target'):
                if action.get(key) in mapping:
                    action[key] = mapping[action[key]]
        for key in ('visual_prompt', 'motion_prompt', 'end_state', 'sfx'):
            if shot.get(key):
                shot[key] = pattern.sub(lambda m: mapping[m.group()], str(shot[key]))
        changed[int(shot.get('index', index))] = mapping
    return changed


def resolved_changes(directory: Path) -> dict[int, dict[str, str]]:
    script = json.loads((directory / 'chapter_script.json').read_text())
    segments = json.loads((directory / 'segments.json').read_text())
    return resolve_script(script, directory.parent, segments, chapter=int(directory.name.rsplit('_', 1)[1]))
