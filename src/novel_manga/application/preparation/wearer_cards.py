"""Check the actual character image before requiring another clothing phase card."""
from __future__ import annotations

import fcntl
from pathlib import Path
import httpx

from novel_manga.llm.client import ask_json, image_part, obj
from novel_manga.review.endpoints import judge_settings
from novel_manga.util import atomic_write_json, read_json

POLICY = 'wearer-card-vision-v1'


def inputs(novel, reference, prop):
    if not reference.get('path'):
        return None
    path = Path(novel) / reference['path']
    if not path.is_file():
        return None
    bible = read_json(Path(novel) / 'story_bible.json', {})
    definition = next((row for row in bible.get('props', []) if row['name'] == prop), {'name': prop})
    stat = path.stat()
    return {'path': reference['path'], 'image': [stat.st_mtime_ns, stat.st_size],
            'wearer': reference['name'], 'prop': definition}


def record_path(novel, reference):
    return (Path(novel) / reference['path']).parent / 'wears_review.json'


def reviewed(novel, reference, prop):
    expected = inputs(novel, reference, prop)
    if not expected:
        return None
    record = read_json(record_path(novel, reference), {}).get(reference['path'] + '|' + prop, {})
    if record.get('policy') == POLICY and record.get('inputs') == expected:
        return record.get('answer')
    return None


def verified(novel, reference, prop):
    return (reviewed(novel, reference, prop) or {}).get('wearing') == 'matches'


def check(novel, reference, prop):
    expected = inputs(novel, reference, prop)
    if not expected:
        return
    path = record_path(novel, reference)
    with path.with_suffix('.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = read_json(path, {})
        key = reference['path'] + '|' + prop
        old = records.get(key, {})
        if old.get('policy') == POLICY and old.get('inputs') == expected:
            return old['answer']
        prompt = ('只核对实际角色参考图是否已经穿着指定物件，不评价未来的视频或角色姿态。'
                  '图片才是依据，物件描述只是待核对目标；不能因为目标里写了就判定图里存在。'
                  'wearing=matches：图中该人物身上能明确看见此服装或穿戴物，并符合明确的颜色、材质与类型；'
                  'different：缺少它、穿着别的物件或明确相反；unclear：遮挡、裁切或细节不足。'
                  '手中拿着或独立摆放不算穿着；普通披风或外套若图中已经穿着，可以matches，不要求额外画一张。'
                  '不因同色系明暗或合理画风差异判相反；不凭想象补出不可见的装备。'
                  f'\n人物：{reference["name"]}\n待核对物件：{expected["prop"]}')
        answer = ask_json([{'type': 'text', 'text': prompt}, image_part(Path(novel) / reference['path'], 1024)],
            obj({'wearing': {'type': 'string', 'enum': ['matches', 'different', 'unclear']},
                 'evidence': {'type': 'string', 'maxLength': 200}}),
            name='wearer_card_vision', max_tokens=500, timeout=180, settings=judge_settings())
        records[key] = {'policy': POLICY, 'inputs': expected, 'answer': answer}
        atomic_write_json(path, records)
        return answer


def check_references(novel, pairs):
    checked = set()
    for reference, prop in pairs:
        if not reference.get('path'):
            continue
        if (reference['path'], prop) not in checked:
            checked.add((reference['path'], prop))
            try:
                check(novel, reference, prop)
            except (RuntimeError, TimeoutError, OSError, ValueError, httpx.HTTPError) as error:
                # No verdict is cached on an unsuccessful call. Admission keeps
                # this pair unverified while unrelated clips can still proceed.
                print(f'wearer card {reference["name"]}/{prop}: check unavailable ({type(error).__name__})', flush=True)
