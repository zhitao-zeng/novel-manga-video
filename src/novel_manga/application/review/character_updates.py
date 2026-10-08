"""Explicit, source-grounded corrections to selected existing character entries."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path

from novel_manga.models.bible import Character, StoryBible
from novel_manga.llm.client import ask_json
from novel_manga.review.endpoints import judge_settings
from novel_manga.story.reading_facts import facts_for_name
from novel_manga.util import atomic_write_json, read_json


def _schema(names: list[str]) -> dict:
    fields = Character.model_json_schema()['properties']
    changes = {'type': 'object', 'additionalProperties': False,
               'properties': {k: v for k, v in fields.items() if k not in {'name', 'voice_profile_id', 'episode_costumes'}},
               'required': []}
    return {'type': 'object', 'additionalProperties': False, 'required': ['characters'],
            'properties': {'characters': {'type': 'array', 'minItems': len(names), 'maxItems': len(names),
                'prefixItems': [{'type': 'object', 'additionalProperties': False,
                    'required': ['name', 'changes', 'evidence', 'reason'],
                    'properties': {'name': {'type': 'string', 'enum': [name]}, 'changes': changes,
                        'evidence': {'type': 'array', 'items': {'type': 'string'}},
                        'reason': {'type': 'string'}}} for name in names], 'items': False}}}


def reconcile_characters(novel_dir: Path, names: list[str], *, character_facts: dict | None = None,
                         chapter: int | None = None, source: str | None = None) -> dict:
    """Correct only an explicitly selected base entry; no images, phases or tasks are dispatched."""
    novel_dir = Path(novel_dir)
    selected = list(dict.fromkeys(names))
    if not selected:
        raise ValueError('select at least one character to update')
    if source is None:
        from novel_manga.ingest import read_source_text, recorded_episodes
        metadata = read_json(novel_dir / 'novel.json', {})
        source = read_source_text(metadata['source'])
        if chapter is not None:
            source = next(ep.source_text for ep in recorded_episodes(source, metadata['chapters']) if ep.index == chapter)
    reading = read_json(novel_dir / 'reading_cast.json', {})
    facts = reading.get('character_facts', {}) if character_facts is None else character_facts
    aliases = read_json(novel_dir / 'bible_aliases.json', {})
    result = {'updated': [], 'unchanged': [], 'rejected': {}, 'changes': {}}
    with (novel_dir / 'story_bible.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = novel_dir / 'story_bible.json'
        original = json.loads(path.read_text(encoding='utf-8'))
        rows = {c['name']: c for c in original['characters']}
        for name in selected:
            if name not in rows:
                raise ValueError(f'not a registered character: {name}')
        for start in range(0, len(selected), 4):
            batch = selected[start:start + 4]
            records = []
            # Context is a contiguous source window, not a guess at its speaker or owner.
            from novel_manga.story.character_evidence import excerpts
            for name in batch:
                fact = facts_for_name(facts, name, aliases=aliases)
                context = excerpts(source, name)
                for alias, owner in aliases.items():
                    if owner == name and alias in source:
                        context += '\n' + excerpts(source, alias, limit=2)
                # Preserved quotations can supply the described-before-naming passages.
                records.append({'name': name, 'current': rows[name], 'reading_facts': fact,
                                'source_context': context[:12000]})
            prompt = ('核对选定人物的基础登记与原文事实，仅修正明确冲突的字段。'
                '当前条目包含已确认的人工修正和美术设计，原文没有明确相反证据时保留。'
                '寿龄与外观年龄分开；永久毁容和非人形态属于外貌。'
                '后文的变身、附体或换装属于阶段状态，不能覆盖基础形态或合并同时出现的人物。'
                '修改appearance时一并核对hair、palette、wardrobe、base_costume、role与signature_prop。'
                'changes为空表示保留；每项实际修正都须在evidence中摘录原文连续原句。'
                '仅返回选定人物，不改变名字、声音绑定或服装阶段，不请求重画。\n'
                + json.dumps(records, ensure_ascii=False))
            try:
                answer = ask_json([{'type': 'text', 'text': prompt}], _schema(batch),
                                  name='bible_character_update', max_tokens=4000, settings=judge_settings())
            except Exception as error:
                for name in batch:
                    result['rejected'][name] = f'{type(error).__name__}: {str(error)[:200]}'
                continue
            returned = answer.get('characters') if isinstance(answer, dict) else None
            candidates = {}
            duplicates = set()
            for row in returned if isinstance(returned, list) else []:
                if not isinstance(row, dict) or not isinstance(row.get('name'), str) or row['name'] not in batch:
                    continue
                if row['name'] in candidates:
                    duplicates.add(row['name'])
                candidates[row['name']] = row
            for name in batch:
                if name in duplicates:
                    result['rejected'][name] = 'model returned duplicate character updates'
                    continue
                row = candidates.get(name)
                if row is None:
                    result['rejected'][name] = 'model omitted selected character'
                    continue
                patch = row.get('changes')
                if not isinstance(patch, dict):
                    result['rejected'][name] = 'changes must be an object'
                    continue
                if not patch:
                    result['unchanged'].append(name)
                    continue
                allowed = _schema([name])['properties']['characters']['prefixItems'][0]['properties']['changes']['properties']
                if set(patch) - set(allowed):
                    result['rejected'][name] = 'patch changes protected or unknown fields'
                    continue
                evidence = row.get('evidence')
                if not isinstance(evidence, list) or not evidence or any(not isinstance(q, str) or not q.strip() or q not in source for q in evidence):
                    result['rejected'][name] = 'changed entry requires verbatim source evidence'
                    continue
                candidate = {**rows[name], **patch}
                try:
                    Character.model_validate(candidate)
                    if not str(candidate.get('appearance') or '').strip() or not str(candidate.get('wardrobe') or '').strip():
                        raise ValueError('appearance and wardrobe must remain usable')
                except ValueError as error:
                    result['rejected'][name] = str(error)[:200]
                    continue
                changes = {k: {'before': rows[name].get(k), 'after': v} for k, v in patch.items() if rows[name].get(k) != v}
                if not changes:
                    result['unchanged'].append(name)
                    continue
                rows[name].update(patch)
                result['updated'].append(name)
                result['changes'][name] = {'fields': changes, 'evidence': evidence, 'reason': row.get('reason', '')}
        if result['updated']:
            StoryBible.model_validate(original)
            atomic_write_json(path, original)
        report = read_json(novel_dir / 'bible_review.json', {})
        report['character_updates'] = result
        atomic_write_json(novel_dir / 'bible_review.json', report)
        fcntl.flock(lock, fcntl.LOCK_UN)
    return result
