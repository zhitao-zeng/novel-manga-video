"""Carry merged reading places into planning, retaining their source chapters."""
from __future__ import annotations

import fcntl
import json

from novel_manga.llm.client import ask_json, obj
from novel_manga.planning.authored_brief import chapter_text
from novel_manga.planning.places import short_of
from novel_manga.util import atomic_write_json, read_json


def adopt(novel_dir, export):
    with (novel_dir/'reading_cast.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        reading = read_json(novel_dir/'reading_cast.json', {})
        places = {r['name']: r for r in reading.get('locations', [])}
        for row in export.get('locations', []):
            name = row['name'].strip()
            chapters = sorted({int(n) for n in [*row.get('chapters', []),
                *places.get(name, {}).get('chapters', [])] if int(n) > 0})
            if not name or not chapters:
                continue
            places[name] = {**row, 'name': name, 'chapters': chapters, 'first_chapter': min(chapters)}
        reading['locations'] = list(places.values())
        atomic_write_json(novel_dir/'reading_cast.json', reading)
    added = []
    with (novel_dir/'story_bible.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        bible = read_json(novel_dir/'story_bible.json')
        growth = read_json(novel_dir/'bible_growth.json', {})
        known = {short_of(row) for row in bible.get('locations', [])}
        for name, row in places.items():
            if name in known:
                continue
            bible.setdefault('locations', []).append(name)
            known.add(name)
            added.append(name)
            growth.setdefault(str(row['first_chapter']), {}).setdefault('locations', []).append(name)
        atomic_write_json(novel_dir/'story_bible.json', bible)
        atomic_write_json(novel_dir/'bible_growth.json', growth)
    return added


def for_chapter(novel_dir, chapter):
    return [row for row in read_json(novel_dir/'reading_cast.json', {}).get('locations', [])
            if chapter in row.get('chapters', [])]


def ensure_designs(novel_dir, asset_ids):
    """Called under the existing card lock; only a required bare plate needs a model."""
    bible = read_json(novel_dir/'story_bible.json')
    evidence = {r['name']: r for r in read_json(novel_dir/'reading_cast.json', {}).get('locations', [])}
    for index, entry in enumerate(bible.get('locations', []), 1):
        if f'location_{index:03d}' not in asset_ids or '：' in entry:
            continue
        name = entry.strip()
        row = evidence.get(name)
        if not row:
            continue  # Existing legacy location handling remains unchanged.
        metadata = read_json(novel_dir/'novel.json')
        passages = [chapter_text(metadata, int(n)) for n in row['chapters'][:3]]
        payload = {'name': name, 'reading_evidence': row, 'source_chapters': passages}
        result = ask_json([{'type': 'text', 'text':
            '根据原文为地点设计一句空场景参考图描写。人物和事件仅用于定位，画面不能含人物、'
            '生物、对白或可读文字。保留原文支持的建筑、材质、空间和自然光；不要把剧情事件'
            '当成固定布景。没有原文支持则grounded=false。\n'+json.dumps(payload, ensure_ascii=False)}],
            obj({'grounded': {'type': 'boolean'}, 'description': {'type': 'string'}}),
            name='reading-location-design', max_tokens=800, timeout=180)
        description = str(result.get('description') or '').strip()
        if result.get('grounded') is not True or not description:
            raise ValueError(f'location design lacks source support: {name}')
        with (novel_dir/'story_bible.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = read_json(novel_dir/'story_bible.json')
            if current['locations'][index-1] == entry:
                current['locations'][index-1] = f'{name}：{description}'
                atomic_write_json(novel_dir/'story_bible.json', current)
