"""Rebuild source facts, a shared entity catalogue and scripts in a separate book.

No legacy Bible, script or review enters the model's source-reading calls.
Old assets remain retrieval candidates, never source evidence. This entry does
not create images, render videos, publish episodes or change the original book.
"""
from __future__ import annotations

import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_REPO / "src"), str(_REPO / "scripts"), str(_REPO)]


import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import copy
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from novel_manga.ingest import read_novel
from novel_manga.models.bible import Character, StoryBible
from novel_manga.runtime_backends import normalize_text
from novel_manga.util import atomic_write_json
from identity_store_thin import read

POLICY = 'source-book-rebuild-v2-independent-check'
ACTOR_KINDS = {'person', 'creature', 'group'}
JUDGE_SLOT = threading.BoundedSemaphore(1)


def setup_models():
    from novel_manga.util import load_dotenv
    load_dotenv(ROOT / '.env')
    os.environ['QWEN38_LOCAL_BASE_URL'] = ','.join(
        [f'http://127.0.0.1:{p}/v1' for p in range(18120, 18125)]
        + ['http://172.28.4.52:18125/v1', 'http://172.28.4.52:18126/v1'])
    os.environ['QWEN38_LOCAL_MODEL'] = 'Qwen3.8-27B-Project'
    os.environ['QWEN38_LOCAL_API_KEY_VAR'] = 'H3_PROMPT_NO_KEY'
    os.environ['NOVEL_CLIP_SECONDS_MAX'] = '15'
    os.environ['QWEN38_LOCAL_MIN_MAX_TOKENS'] = '6000'


def judge_json(parts, schema, *, name, max_tokens=2200, timeout=240):
    """Independent configured judge, one in flight, without changing the reader's backend."""
    import httpx
    from novel_manga.llm.transport import stream_completion
    # Import during setup/main has already selected the local reader. Only
    # read the alternate configuration here; do not mutate process settings.
    from novel_manga.review.endpoints import JUDGES
    config = JUDGES['flashnext']
    key = os.environ.get(config['QWEN38_LOCAL_API_KEY_VAR'], '')
    payload = {'model': config['QWEN38_LOCAL_MODEL'], 'temperature': 0, 'max_tokens': max_tokens, 'reasoning_effort': 'low',
               'response_format': {'type': 'json_schema', 'json_schema': {'name': name, 'strict': True, 'schema': schema}},
               'messages': [{'role': 'user', 'content': parts}]}
    with JUDGE_SLOT, httpx.Client(trust_env=False, timeout=timeout) as client:
        result = stream_completion(client, config['QWEN38_LOCAL_BASE_URL'] + '/chat/completions',
                                   {'Authorization': 'Bearer ' + key} if key else {}, payload, timeout=timeout)
    from planner_requests_thin import extract_json
    choice = result['choices'][0]
    if choice.get('finish_reason') != 'stop' or not choice['message'].get('content'):
        raise ValueError('independent judge did not finish: ' + str(choice.get('finish_reason')))
    return extract_json(choice['message']['content'])


def fact_schema(count):
    from novel_manga.llm.client import obj
    text = {'type': 'string'}
    refs = {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 1, 'maximum': count}}
    aid = {'type': 'integer', 'minimum': 1, 'maximum': 40}
    return obj({
        'source_readable': {'type': 'boolean'}, 'source_problem': text,
        'actors': {'type': 'array', 'maxItems': 40, 'items': obj({
            'id': aid, 'name': text, 'named': {'type': 'boolean'},
            'kind': {'type': 'string', 'enum': ['person', 'creature', 'group', 'object', 'concept']},
            'presence': {'type': 'string', 'enum': ['on_stage', 'voice', 'mentioned']},
            'forms': {'type': 'array', 'minItems': 1, 'items': obj({
                'text': text, 'kind': {'type': 'string', 'enum': ['name', 'role', 'pronoun']}, 'paragraphs': refs})},
            'body_facts': {'type': 'array', 'maxItems': 5, 'items': obj({'description': text, 'paragraphs': refs})}})},
        'locations': {'type': 'array', 'minItems': 1, 'maxItems': 12,
                      'items': obj({'name': text, 'description': text, 'paragraphs': refs})},
        'events': {'type': 'array', 'minItems': 1, 'maxItems': 24, 'items': obj({
            'summary': text, 'paragraphs': refs,
            'participants': {'type': 'array', 'items': aid},
            'speech': {'type': 'array', 'maxItems': 8, 'items': obj({
                'actor': aid, 'quote': text, 'mode': {'type': 'string', 'enum': ['spoken', 'thought', 'message']}})}})},
    })


FACT_RULES = (
    '从原文建立事实底稿，不写剧本、不读旧人物库。先逐段列实际主体，再按顺序记核心事件及说话归属。'
    '每个有动作、对白、聊天消息的主体都必须出现；不能为了主角而省掉只说一句话的人。'
    'actors的id仅本章有效。同一人的残留旧名、改名、称呼可合在forms；由连续动作和问答确定，不能仅凭名字相似。'
    '分别行动或对话的分身、身体、双胞胎分开。named只有真正的专名才为true；代词只作为主体的局部指称，不能单列为独立实体。'
    'kind区分人、其他有生命或意识的主体、群体、器物、抽象概念；动物、灵植、有意识的器灵可为creature，'
    '普通武器、丹药、储物空间不是演员。forms保留原文确实用过的叫法和编号，不编造。'
    'forms.kind=name只用于真正专名或专用网名；爸爸、主人、哥哥等称谓填role，代词填pronoun，不能把称谓当全书通用的别名。'
    'body_facts只写原文明示的外形、物种、性别、衣着和形态变化；未说明就空数组，不能补成人形。'
    '同章变身前后分别引用对应段落。locations只写本章发生的场景，原文没具体地名可用场景描述，不套别处场景。'
    'events记录核心动作、因果和对话归属，participants只填实际参加该事件的主体id；'
    'speech摘取该事件关键原话，尤其是提问、回答、命令、承诺、消息；quote须逐字存在于对应原文段落，'
    '不能将旁白、心理或收到的消息改成他人说话。事实允许后续压缩表达，但不能更换发起者和受事者。'
    '所有paragraphs使用下面的编号。断行、旧名残留不算不可读；无法理解连续事件的严重乱码才填false。'
    '直接返回JSON，不写思考过程。\n'
)


def paragraph_text(segments, refs):
    if not refs or any(type(i) is not int or not 1 <= i <= len(segments) for i in refs):
        raise ValueError('source evidence refers to absent paragraphs')
    return '\n'.join(segments[i - 1]['text'] for i in sorted(set(refs)))


def ground_speech_quote(text, source):
    key = normalize_text(text)
    if not key:
        raise ValueError('empty speech quotation')
    if key in normalize_text(source):
        return [text]
    # One utterance may be interrupted by narration: “甲。”他说，“乙。”
    # Keep the separate literal pieces instead of pretending the joined line
    # is a contiguous source quotation. Attribution is still checked separately.
    pieces = [m[1] for m in re.finditer(r'[“「](.*?)[”」]', source, re.S)]
    keys = [normalize_text(p) for p in pieces]
    for start in range(len(keys)):
        joined = ''
        for end in range(start, len(keys)):
            joined += keys[end]
            if key in joined:
                return pieces[start:end + 1]
            if len(joined) > len(key) * 2:
                break
    raise ValueError('speech quotation not found in event source')


def independent_form(form, source, longer_names):
    text, key = normalize_text(source), normalize_text(form)
    if not key:
        return False
    covered = []
    for name in longer_names:
        longer = normalize_text(name)
        if len(longer) <= len(key) or key not in longer:
            continue
        start = text.find(longer)
        while start >= 0:
            covered.append((start, start + len(longer)))
            start = text.find(longer, start + 1)
    start = text.find(key)
    while start >= 0:
        if not any(lo <= start and start + len(key) <= hi for lo, hi in covered):
            return True
        start = text.find(key, start + 1)
    return False


def validate_facts(facts, segments):
    if not facts.get('source_readable'):
        raise ValueError('source reading not usable: ' + str(facts.get('source_problem', '')))
    actors = {a['id']: a for a in facts['actors']}
    if len(actors) != len(facts['actors']):
        raise ValueError('duplicate local actor ids')
    explicit_names = {a['name'] for a in actors.values() if a['named']}
    for actor in actors.values():
        forms = []
        for form in actor['forms']:
            quote = paragraph_text(segments, form['paragraphs'])
            if (normalize_text(form['text']) and normalize_text(form['text']) in normalize_text(quote)
                    and (form['kind'] != 'name' or independent_form(form['text'], quote, explicit_names))):
                forms.append(form)
        if not forms:
            raise ValueError('actor has no source-grounded name: ' + actor['name'])
        actor['forms'] = forms
        if actor['named'] and not any(f['kind'] == 'name' and normalize_text(f['text']) == normalize_text(actor['name']) for f in forms):
            raise ValueError('named actor lacks an actual proper name: ' + actor['name'])
        for fact in actor['body_facts']:
            fact['source_quote'] = paragraph_text(segments, fact['paragraphs'])
    for loc in facts['locations']:
        loc['source_quote'] = paragraph_text(segments, loc['paragraphs'])
    coverage = set()
    for event in facts['events']:
        quote = paragraph_text(segments, event['paragraphs'])
        coverage.update(event['paragraphs'])
        if set(event['participants']) - actors.keys():
            raise ValueError('event uses an absent actor')
        for speech in event['speech']:
            if speech['actor'] not in event['participants']:
                raise ValueError('speaker absent from event participants')
            speech['source_quotes'] = ground_speech_quote(speech['quote'], quote)
            if actors[speech['actor']]['kind'] not in ACTOR_KINDS:
                raise ValueError('speech assigned to a non-actor')
    if coverage != set(range(1, len(segments) + 1)):
        raise ValueError('facts omit source paragraphs: ' + str(sorted(set(range(1, len(segments) + 1)) - coverage)))
    return facts


def check_facts(facts, segments):
    from novel_manga.llm.client import obj
    # Show the actual names beside every reference. A judge repeatedly read
    # actor=1 as the second list entry and invented speaker swaps in valid data.
    names = {a['id']: a['name'] for a in facts['actors']}
    view = copy.deepcopy(facts)
    for i, event in enumerate(view['events'], 1):
        event['event_number'] = i
        event['participants'] = [names[n] for n in event['participants']]
        for j, speech in enumerate(event['speech'], 1):
            speech['speech_number'] = j
            speech['actor'] = names[speech['actor']]
    answer = judge_json([{'type': 'text', 'text':
        '独立检查原文事实底稿，不评价画面。逐段查：是否遗漏行动者/说话者/聊天发信者；'
        '是否把器物或代词当独立人物；是否合并分别行动的人；是否把同一人的旧名拆成两人；'
        '台词和动作是否归错主体；是否漏掉核心事件或捏造外形、地点、形态。'
        '只报有原文依据的明确矛盾，不要求收录每句台词、吐槽或支线；名单里仅被提及而没有参与的人不必逐个建条目。动作已经记在events就不是遗漏，'
        'body_facts只存外形和形态，不要求重复动作。旧名/改名在连续叙事中同指一人时不可反复换绑。'
        '底稿中的说话者已经展开为姓名，必须读实际actor值，不能误按列表顺序猜编号。'
        'kind=speaker时指出event_number、speech_number及correct_actor（正确主体姓名）。其他问题编号填0，correct_actor留空。'
        'source_paragraphs填原文区段编号，由程序提取真实引文；说话者纠错的段落必须包含正确说话人的名字或本章专指称谓。'
        '没有确定错误就issues=[]。problem仅写简短的明确结论，不输出思考过程，不写自我反驳或猜测。\n'
        + json.dumps({'source': segments, 'facts': view}, ensure_ascii=False)}],
        obj({'issues': {'type': 'array', 'maxItems': 12, 'items': obj({
            'kind': {'type': 'string', 'enum': ['speaker', 'identity', 'missing_actor', 'event', 'appearance', 'location']},
            'event_number': {'type': 'integer', 'minimum': 0}, 'speech_number': {'type': 'integer', 'minimum': 0},
            'correct_actor': {'type': 'string'},
            'problem': {'type': 'string', 'maxLength': 240},
            'source_paragraphs': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 1, 'maximum': len(segments)}}})}}),
        name='source_facts_check', max_tokens=2200, timeout=240)
    kept, discarded = [], []
    for row in answer['issues']:
        row['source_quote'] = paragraph_text(segments, row['source_paragraphs'])
        if row['kind'] == 'speaker':
            i, j = row['event_number'] - 1, row['speech_number'] - 1
            if not 0 <= i < len(facts['events']) or not 0 <= j < len(facts['events'][i]['speech']):
                raise ValueError('fact check cited absent dialogue')
            actual = facts['events'][i]['speech'][j]['actor']
            actor = next(a for a in facts['actors'] if a['id'] == actual)
            if row['correct_actor'] in {actor['name'], *(f['text'] for f in actor['forms'])}:
                discarded.append(row)  # the proposed correction is already the saved attribution
                continue
            target = next((a for a in facts['actors'] if a['name'] == row['correct_actor']), None)
            target_forms = {target['name'], *(f['text'] for f in target['forms'] if f['kind'] != 'pronoun')} if target else {row['correct_actor']}
            if not any(normalize_text(form) and normalize_text(form) in normalize_text(row['source_quote']) for form in target_forms):
                raise ValueError('speaker correction does not name its proposed owner in source evidence')
        kept.append(row)
    return {'issues': kept, 'already_correct': discarded}


def extract_one(directory, chapter):
    manifest = read(directory / 'rebuild/manifest.json')
    segments = read(directory / f'{directory.name}_{chapter}/segments.json')
    path = directory / f'rebuild/facts/{chapter}.json'
    saved = read(path, {})
    if saved.get('policy') == POLICY and saved.get('segments') == segments and saved.get('status') == 'verified':
        return saved
    from novel_manga.llm.client import ask_json
    payload = FACT_RULES + json.dumps([{'paragraph': i, **s} for i, s in enumerate(segments, 1)], ensure_ascii=False)
    errors = []
    # Retain the earlier source reads. An invalid judge must not force an
    # otherwise correct extraction to be regenerated and possibly degraded.
    if saved.get('status') == 'needs_attention' and saved.get('segments') == segments:
        for old_path in sorted((directory / 'rebuild/raw').glob(f'{chapter}-[12].json')):
            try:
                answer = read(old_path)
                validate_facts(answer, segments)
                check = check_facts(answer, segments)
                atomic_write_json(directory / f'rebuild/raw/{chapter}-{POLICY}-{old_path.stem}-check.json', check)
                if check['issues']:
                    errors.append(check['issues'])
                    continue
                result = {'policy': POLICY, 'chapter': chapter, 'at': time.strftime('%F %T'), 'status': 'verified',
                          'segments': segments, 'facts': answer, 'reused_source_read': str(old_path), 'prior_errors': errors,
                          'judge': 'Qwen3.8-Flash-Next'}
                atomic_write_json(path, result)
                return result
            except Exception as error:
                errors.append(str(error)[:500])
    for attempt in range(1, 3):
        try:
            answer = ask_json([{'type': 'text', 'text': payload + (
                '\n前次检查发现以下问题，请重新完整读取原文并修正，不只修补一个字段：\n' + json.dumps(errors[-1], ensure_ascii=False) if errors else '')}],
                fact_schema(len(segments)), name='source_facts_read', max_tokens=7000, timeout=300)
            atomic_write_json(directory / f'rebuild/raw/{chapter}-{POLICY}-{attempt}.json', answer)
            validate_facts(answer, segments)
            check = check_facts(answer, segments)
            atomic_write_json(directory / f'rebuild/raw/{chapter}-{POLICY}-{attempt}-check.json', check)
            if check['issues']:
                errors.append(check['issues'])
                continue
            result = {'policy': POLICY, 'chapter': chapter, 'at': time.strftime('%F %T'), 'status': 'verified',
                      'segments': segments, 'facts': answer, 'attempts': attempt, 'prior_errors': errors,
                      'judge': 'Qwen3.8-Flash-Next'}
            atomic_write_json(path, result)
            return result
        except Exception as error:
            errors.append(str(error)[:500])
    result = {'policy': POLICY, 'chapter': chapter, 'at': time.strftime('%F %T'), 'status': 'needs_attention',
              'segments': segments, 'errors': errors}
    atomic_write_json(path, result)
    return result


def init_book(source, old_book, directory, excluded):
    if directory.resolve() == old_book.resolve():
        raise ValueError('rebuild must use a separate directory')
    manifest_path = directory / 'rebuild/manifest.json'
    if manifest_path.exists():
        saved = read(manifest_path)
        if saved['source'] != str(source.resolve()) or saved['excluded'] != sorted(excluded):
            raise ValueError('existing rebuild uses different source or scope')
        return saved
    from novel_manga.planning.text import split_segments
    from novel_manga.planning.constants import SEGMENT_COUNT
    novel = read_novel(source, novel_id=directory.name, title='诸天万象录')
    directory.mkdir(parents=True, exist_ok=True)
    source_stat = source.stat()
    manifest = {'policy': POLICY, 'source': str(source.resolve()), 'source_stat': [source_stat.st_mtime_ns, source_stat.st_size],
                'legacy_book': str(old_book.resolve()), 'title': novel.title, 'excluded': sorted(excluded),
                'facts_scope': [e.index for e in novel.episodes if e.index not in excluded],
                'planning_scope': [e.index for e in novel.episodes if 2001 <= e.index <= 3848 and e.index not in excluded],
                'created_at': time.strftime('%F %T'), 'original_book_read_only': True}
    for episode in novel.episodes:
        if episode.index in excluded:
            continue
        atomic_write_json(directory / f'{directory.name}_{episode.index}/segments.json',
                          split_segments(episode.source_text, episode.source_title, SEGMENT_COUNT))
    atomic_write_json(directory / 'novel.json', {'novel_id': directory.name, 'title': novel.title,
        'source': str(source.resolve()), 'chapters': [{'index': e.index, 'title': e.source_title, 'chars': e.text_count} for e in novel.episodes]})
    old_profile = read(old_book / 'profile.json', {})
    atomic_write_json(directory / 'profile.json', {'style': old_profile.get('style', '3d'), 'frame': '16:9',
                      'tier': 'fast', 'genre': 'generic', 'speech_gate': 'observe'})
    grammar = read(ROOT / 'configs/templates/visual_grammar.json', {})
    grammar['location_time'] = {}
    atomic_write_json(directory / 'visual_grammar.json', grammar)
    atomic_write_json(manifest_path, manifest)
    return manifest


def compile_book(directory, chapters):
    """Shared named identities; unnamed people remain chapter-local roles."""
    records = [read(directory / f'rebuild/facts/{n}.json', {}) for n in sorted(chapters)]
    if any(r.get('status') != 'verified' for r in records):
        raise ValueError('cannot compile an unverified fact batch')
    parent = {}
    actual_names = {a['name'] for r in records for a in r['facts']['actors'] if a['named'] and a['kind'] in ACTOR_KINDS}
    def find(x):
        parent.setdefault(x, x)
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    for r in records:
        for a in r['facts']['actors']:
            if not a['named'] or a['kind'] not in ACTOR_KINDS:
                continue
            names = [a['name'], *(f['text'] for f in a['forms'] if f['kind'] == 'name' and f['text'] in actual_names)]
            for name in names:
                parent[find(name)] = find(a['name'])
    groups = {}
    for name in parent:
        groups.setdefault(find(name), set()).add(name)
    names_seen = Counter(a['name'] for r in records for a in r['facts']['actors'] if a['named'] and a['kind'] in ACTOR_KINDS)
    canonical = {name: sorted(group, key=lambda x: (-names_seen[x], len(x), x))[0]
                 for group in groups.values() for name in group}
    from novel_manga.llm.client import obj
    alias_checks = []
    for r in records:
        owners = [canonical[a['name']] for a in r['facts']['actors'] if a['named'] and a['kind'] in ACTOR_KINDS]
        repeated = sorted(name for name, count in Counter(owners).items() if count > 1)
        for group_number, target_name in enumerate(repeated, 1):
            actors = [a for a in r['facts']['actors'] if a['named'] and a['kind'] in ACTOR_KINDS and canonical[a['name']] == target_name]
            target_names = {a['name'] for a in actors}
            evidence = []
            for other in records:
                if other['chapter'] == r['chapter']:
                    continue
                for a in other['facts']['actors']:
                    forms = {f['text'] for f in a['forms'] if f['kind'] == 'name'}
                    if target_names <= forms:
                        quotes = [s['text'] for s in other['segments'] if all(normalize_text(n) in normalize_text(s['text']) for n in target_names)]
                        if quotes:
                            evidence.append({'chapter': other['chapter'], 'source_quote': quotes[0]})
            inputs = {'names_to_compare': sorted(target_names),
                      'source': [{'paragraph': i, **s} for i, s in enumerate(r['segments'], 1)],
                      'other_source_examples': evidence[:3]}
            path = directory / f'rebuild/alias-checks/{r["chapter"]}-{group_number}.json'
            cached = read(path, {})
            if cached.get('inputs') == inputs:
                answer = cached['answer']
            else:
                answer = judge_json([{'type': 'text', 'text':
                    '本次只比较names_to_compare列出的名字是否指同一实体，其他称谓和角色不在问题范围。'
                    '原文读取有冲突：有章节把这些名字归为同一个人的写法，本章却拆成多条。'
                    '请从原文动作连续性、对话问答和是否确实分别行动判断。名字同时出现不等于两个独立身体，'
                    '但分身、本体、附身者与身体主人不能当普通别名合并。只按原文，不按名字相似或外貌猜。'
                    '文本残留改名不要求人物自己解释改过名字；连续叙事中同一人的行动和问答使用两个名字，可作为同指证据。'
                    '只有本章这些条目确实是同一实体的残留改名/别称才填same_entity；明确分别行动填different；'
                    '不能确认填uncertain。paragraphs引用本章原文编号作为证据。\n' + json.dumps(inputs, ensure_ascii=False)}],
                    obj({'verdict': {'type': 'string', 'enum': ['same_entity', 'different', 'uncertain']},
                         'reason': {'type': 'string', 'maxLength': 240},
                         'paragraphs': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 1, 'maximum': len(r['segments'])}}}),
                    name='source_alias_conflict', max_tokens=12000)
                quote = paragraph_text(r['segments'], answer['paragraphs'])
                if not all(normalize_text(a['name']) in normalize_text(quote) for a in actors):
                    raise ValueError('alias evidence does not name the proposed identities')
                atomic_write_json(path, {'inputs': inputs, 'answer': answer, 'source_quote': quote})
            alias_checks.append({'chapter': r['chapter'], **answer})
            if answer['verdict'] != 'same_entity':
                raise ValueError(f"chapter {r['chapter']}: proposed global aliases collapse distinct or unresolved source actors")
    manifest = read(directory / 'rebuild/manifest.json')
    legacy = read(Path(manifest['legacy_book']) / 'story_bible.json', {})
    old_ids = {c['name']: f'character_{i:03d}' for i, c in enumerate(legacy.get('characters', []), 1)}
    entities = []
    for i, name in enumerate(sorted(set(canonical.values())), 1):
        forms = sorted(k for k, v in canonical.items() if v == name)
        occurrences = [{'chapter': r['chapter'], 'source_actor': a['id'], 'body_facts': a['body_facts'], 'kind': a['kind']}
                       for r in records for a in r['facts']['actors'] if a['named'] and a['kind'] in ACTOR_KINDS and canonical[a['name']] == name]
        entities.append({'id': f'e{i:04d}', 'canonical': name, 'forms': forms, 'source': 'verified_source_facts',
                         'asset_id': f'character_{i:03d}', 'occurrences': occurrences,
                         'legacy_asset_candidates': [{'name': f, 'asset_id': old_ids[f]} for f in forms if f in old_ids]})
    characters = [Character(name=e['canonical'], role='原文主体', gender='原文未明示', age='原文未明示',
                             appearance='', wardrobe='') for e in entities]
    locations = sorted({loc['name'] for r in records for loc in r['facts']['locations']})
    from thin_profile import STYLE_VISUAL
    bible = StoryBible(novel_title=manifest['title'], genre='依据原文逐场确定时代与场景', visual_style=STYLE_VISUAL['3d'],
                      palette='沿用项目画风，具体服装、物种与时代服从当前原文', style_fingerprint=directory.name,
                      characters=characters, locations=locations,
                      continuity_rules=['原文事实与美术设计分开；未说明的外形不是已知事实。', '当前形态按章节内事件先后，不把后期人形用于变身前。'])
    atomic_write_json(directory / 'story_bible.json', bible.model_dump(mode='json'))
    atomic_write_json(directory / 'entity/entities.json', entities)
    atomic_write_json(directory / 'bible_aliases.json', {})  # source bindings stay chapter-local
    atomic_write_json(directory / 'rebuild/catalogue.json', {'policy': POLICY, 'chapters': sorted(chapters), 'entities': entities,
        'alias_checks': alias_checks,
        'objects': [{'chapter': r['chapter'], **a} for r in records for a in r['facts']['actors'] if a['kind'] in {'object', 'concept'}]})
    by_name = {e['canonical']: e for e in entities}
    from novel_manga.story.source_identity import POLICY as IDENTITY_POLICY
    from identity_store_thin import chapter_inputs
    for r in records:
        n = r['chapter']; d = directory / f'{directory.name}_{n}'
        names, mentions, appearances, local_cast, actors = {}, [], [], [], []
        participants = {aid for event in r['facts']['events'] for aid in event['participants']}
        for a in r['facts']['actors']:
            if a['kind'] not in ACTOR_KINDS:
                continue
            name = canonical[a['name']] if a['named'] else a['name']
            eid = by_name[name]['id'] if a['named'] else f'local:{n}:{a["id"]}'
            names[eid] = name
            description = '；'.join(f"第{','.join(map(str, f['paragraphs']))}段：{f['description']}" for f in a['body_facts'])
            if not any(c.name == name for c in local_cast):
                local_cast.append(Character(name=name, role='原文主体', gender='原文未明示', age='原文未明示', appearance=description, wardrobe=''))
            # Participation in a verified event must also make an actor
            # available to planning, even if the reader mislabeled presence.
            # Availability never requires the actor to be visible in every shot.
            presence = 'on_stage' if a['id'] in participants and a['presence'] == 'mentioned' else a['presence']
            forms = []
            for f in a['forms']:
                form = {'form': f['text'], 'kind': 'proper' if f['kind'] == 'name' else 'contextual', 'paragraphs': f['paragraphs']}
                forms.append(form)
                mentions.append({**form, 'entity_id': eid, 'presence': presence, 'source_actor': a['id'],
                                 'entity_kind': 'group' if a['kind'] == 'group' else 'individual', 'count': 0,
                                 'source_quote': paragraph_text(r['segments'], f['paragraphs'])})
            for f in a['body_facts']:
                appearances.append({'entity_id': eid, **f})
            actors.append({'source_id': a['id'], 'name': name, 'kind': 'group' if a['kind'] == 'group' else 'individual',
                           'count': 0, 'presence': presence, 'forms': forms,
                           'appearance': description, 'paragraphs': sorted({p for f in forms for p in f['paragraphs']})})
        local_bible = bible.model_copy(update={'characters': local_cast,
            'locations': [loc['name'] + '：' + loc['description'] for loc in r['facts']['locations']]})
        atomic_write_json(d / 'source_bible.json', local_bible.model_dump(mode='json'))
        atomic_write_json(d / 'identity_context.json', {'policy': IDENTITY_POLICY, 'inputs': chapter_inputs(d), 'chapter': n,
            'at': time.strftime('%F %T'), 'entities': names, 'mentions': mentions, 'appearances': appearances,
            'source_actors': actors, 'unmatched_actors': [], 'relations': [], 'primary_entities': [],
            'actorless_confirmed': not actors, 'source_fact_policy': POLICY})
    return {'characters': len(entities), 'chapters': len(records), 'locations': len(locations)}


def source_bindings(directory, chapter):
    from identity_store_thin import current_context
    from identity_context_thin import effective_aliases
    context = current_context(directory / f'{directory.name}_{chapter}')
    return effective_aliases(directory, chapter, context) if context else {}


def check_script(directory, chapter):
    from novel_manga.llm.client import obj
    d = directory / f'{directory.name}_{chapter}'
    facts = read(directory / f'rebuild/facts/{chapter}.json')
    script = read(d / 'chapter_script.json')
    view = {'shots': [{**{k: v for k, v in shot.items() if k != 'source_quote'}, 'stage': i}
                      for i, shot in enumerate(script['shots'], 1)]}
    answer = judge_json([{'type': 'text', 'text':
        '对照原文独立验收新剧本。只检查明确的剧情错误：动作/台词/消息安错人，器物成为人物，'
        '变身前后形态错用，核心因果断裂或捏造，明确地点相反。允许压缩、单人镜头、听者在画外和聊天卡，'
        '不能因为characters只放说话人就要求所有在场人物每镜都露脸。先查角色是否已在对白、画面、listeners、'
        'actions表达；verified_name_bindings是已经单独按原文核实的同人叫法，不得再次因字面名字不同判成错人。'
        '只用shots中明确给出的stage编号，不要自行数origin_index。每个issue给原文source_paragraphs区段编号和对应阶段，引用由程序摘取，无法举证则不报。\n'
        + json.dumps({'source': facts['segments'], 'facts': facts['facts'], 'script': view,
                      'verified_name_bindings': source_bindings(directory, chapter)}, ensure_ascii=False)}],
        obj({'issues': {'type': 'array', 'maxItems': 12, 'items': obj({
            'stage': {'type': 'integer', 'minimum': 0}, 'problem': {'type': 'string'},
            'source_paragraphs': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 1, 'maximum': len(facts['segments'])}}})}}), name='rebuilt_script_source_check', max_tokens=12000, timeout=240)
    for issue in answer['issues']:
        issue['source_quote'] = paragraph_text(facts['segments'], issue['source_paragraphs'])
    return answer


def check_episode_story(script, segments, facts, name_bindings=None):
    """Read only what the script presents, then compare the understood story."""
    from novel_manga.llm.client import obj
    visible = [{'stage': i, **{k: shot[k] for k in ['characters', 'in_frame', 'listeners', 'location',
                'visual_prompt', 'motion_prompt', 'actions', 'extras', 'turns', 'end_state', 'sfx'] if k in shot}}
               for i, shot in enumerate(script.get('shots', []), 1)]
    bound_names = sorted({name for s in visible for name in [*s.get('characters', []), *s.get('in_frame', []),
                          *s.get('listeners', []), *(t.get('speaker_name', '') for t in s.get('turns', []))] if name})
    text = {'type': 'string'}
    understood = judge_json([{'type': 'text', 'text':
        '你没有读过原著，只能看到以下按播放顺序排列的剧本画面、动作和台词。复述这一集实际讲出的故事：'
        '谁想做什么、因为什么发生冲突、谁对谁采取行动、怎样导致下一步、最后结果是什么。'
        '不得从题材常识补剧情，不得替含混的人物指代找借口；看不出时明确写无法确定。'
        '特别注意：characters/in_frame及turns.speaker_name是实际演员绑定，不能被visual_prompt/motion_prompt里的其他名字覆盖。'
        '例如演员绑定甲而画面文字写乙，必须记录矛盾，不能擅自把甲认成乙。'
        'actor_roles逐个说明实际绑定的演员扮演了什么角色、做了什么事；只能使用提供的绑定名。'
        '允许这是连载的一集，不要求补齐前传；但本集敌我、动作对象和主要因果须能理解。'
        '只输出简短结论，不写思考过程。\n' + json.dumps({'actual_bound_names': bound_names, 'stages': visible}, ensure_ascii=False)}],
        obj({'story': text, 'beats': {'type': 'array', 'maxItems': 12, 'items': obj({
            'who': text, 'action_and_result': text, 'stages': {'type': 'array', 'items': {'type': 'integer'}}})},
            'actor_roles': {'type': 'array', 'maxItems': len(bound_names), 'items': obj({
                'actor': {'type': 'string', **({'enum': bound_names} if bound_names else {})}, 'role_and_actions': text})},
            'unclear_connections': {'type': 'array', 'maxItems': 8, 'items': text}}),
        name='episode_story_cold_read', max_tokens=2000, timeout=240)
    verdict = judge_json([{'type': 'text', 'text':
        '下面是一个未读原著的审读者仅依据剧本可见可听内容复述出的整集故事。现在对照原文和事实底稿，'
        '判断剧本有没有把本集主线讲明白：核心冲突、敌我身份、关键动作归属、因果先后、结局必须一致。'
        '允许合理压缩、换镜头、听者画外、用聊天卡表达、承接前集；不要求每句话或一般支线都出现。'
        '不能因为逐段都通顺就放过整集角色被替代、核心因果丢失；也不能把原文知识替审读者补回去。'
        '尤其核对actor_roles里的实际绑定演员是否扮演了别人的角色；不能只看story复述正确就忽略演员绑定矛盾。'
        'verified_name_bindings里已按原文确认的同人叫法不算换演员，不要因原文用旧名就反复要求改回去。'
        'story_ok=false必须说明具体问题，附原文source_paragraphs区段编号和相关阶段编号；程序摘取引用，整集缺失可填stage=0。'
        '没有明确错误则story_ok=true且issues=[]。\n'
        + json.dumps({'source': segments, 'facts': facts, 'actual_bound_names': bound_names,
                      'verified_name_bindings': name_bindings or {},
                      'understood_story': understood}, ensure_ascii=False)}],
        obj({'story_ok': {'type': 'boolean'}, 'issues': {'type': 'array', 'maxItems': 8, 'items': obj({
            'stage': {'type': 'integer', 'minimum': 0, 'maximum': len(visible)},
            'problem': {'type': 'string', 'maxLength': 240},
            'source_paragraphs': {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 1, 'maximum': len(segments)}}})}}),
        name='episode_story_source_compare', max_tokens=1800, timeout=240)
    if verdict['story_ok'] == bool(verdict['issues']):
        raise ValueError('whole-story verdict contradicts its evidence')
    for issue in verdict['issues']:
        issue['source_quote'] = paragraph_text(segments, issue['source_paragraphs'])
    return {'cold_read': understood, **verdict}


def plan_one(directory, chapter, *, review_only=False):
    d = directory / f'{directory.name}_{chapter}'; path = d / 'source_plan_validation.json'
    facts = read(directory / f'rebuild/facts/{chapter}.json')['facts']
    inputs = {'facts': facts, 'chapter_bible': read(d / 'source_bible.json')}
    saved = read(path, {})
    if (saved.get('policy') == POLICY and saved.get('status') == 'passed'
            and saved.get('whole_story_ok') is True and saved.get('inputs') == inputs):
        return saved
    manifest = read(directory / 'rebuild/manifest.json')
    notes = '以下是独立读取、复核过的原文事实。人物与器物分开；所有核心说话者都可选，不能因镜头只画一人就替换说话者。形态按段落先后。\n' + json.dumps(facts, ensure_ascii=False)
    command = [sys.executable, str(ROOT / 'scripts/plan_chapter_thin.py'), manifest['source'], '--novel-id', directory.name,
               '--title', manifest['title'], '--episode-index', str(chapter), '--bible', str(d / 'source_bible.json'),
               '--output-root', str(directory.parent), '--tier', 'fast', '--frame', '16:9', '--style', '3d',
               '--max-redo', '1', '--timeout', '360', '--max-tokens', '12000', '--notes', notes]
    started = time.monotonic()
    exit_code = 0
    if not review_only:
        with (d / 'source_replan.log').open('a') as log:
            result = subprocess.run(command, cwd=ROOT, env={**os.environ, 'PYTHONPATH': 'src:scripts'},
                                    stdout=log, stderr=subprocess.STDOUT, timeout=1500)
            exit_code = result.returncode
    report = read(d / 'chapter_script_report.json', {})
    if exit_code or report.get('status') != 'passed':
        validation = {'status': 'planning_failed', 'exit_code': exit_code, 'errors': report.get('errors', [])}
    else:
        check = check_script(directory, chapter)
        validation = {'status': 'needs_revision' if check['issues'] else 'passed', **check}
        if not check['issues']:
            story = check_episode_story(read(d / 'chapter_script.json'), read(d / 'segments.json'), facts,
                                        source_bindings(directory, chapter))
            atomic_write_json(d / 'episode_story_check.json', story)
            validation['whole_story_ok'] = story['story_ok']
            if not story['story_ok']:
                validation.update(status='needs_revision', issues=story['issues'])
    validation.update(policy=POLICY, inputs=inputs, at=time.strftime('%F %T'), chapter=chapter, seconds=round(time.monotonic()-started, 2))
    atomic_write_json(path, validation)
    return validation


def run_batch(directory, chapters, stage, workers):
    stop = False
    def pause(*args):
        nonlocal stop
        stop = True
    signal.signal(signal.SIGTERM, pause)
    signal.signal(signal.SIGINT, pause)
    job = extract_one if stage == 'facts' else (
        (lambda directory, chapter: plan_one(directory, chapter, review_only=True)) if stage == 'review' else plan_one)
    rows, pending = {}, iter(chapters)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        active = {}
        while True:
            while len(active) < workers and not stop:
                n = next(pending, None)
                if n is None:
                    break
                active[pool.submit(job, directory, n)] = n
            state = {'policy': POLICY, 'at': time.strftime('%F %T'), 'pid': os.getpid(), 'stage': stage,
                     'status': 'pausing' if stop else 'running', 'total': len(chapters),
                     'completed': len(rows), 'counts': dict(Counter(r.get('status', 'error') for r in rows.values())),
                     'active': sorted(active.values())}
            atomic_write_json(directory / f'rebuild/{stage}-status.json', state)
            if not active:
                state['status'] = 'paused' if stop else 'finished'
                atomic_write_json(directory / f'rebuild/{stage}-status.json', state)
                break
            completed, _ = wait(active, timeout=5, return_when=FIRST_COMPLETED)
            if not completed:
                continue
            done = next(iter(completed))
            n = active.pop(done)
            try:
                rows[n] = done.result()
            except Exception as error:
                rows[n] = {'chapter': n, 'status': 'error', 'error': str(error)[:500]}
                atomic_write_json(directory / f'rebuild/errors/{stage}-{n}.json', rows[n])
            print(json.dumps({'chapter': n, 'stage': stage, 'status': rows[n].get('status'), 'at': time.strftime('%F %T')}, ensure_ascii=False), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['init', 'facts', 'compile', 'plan', 'review'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--legacy-book', type=Path)
    parser.add_argument('--novel-dir', type=Path, required=True)
    parser.add_argument('--chapters', default='all')
    parser.add_argument('--exclude', default='2661-2670')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    setup_models()
    directory = args.novel_dir.resolve()
    from entity_ledger_thin import parse_chapters
    if args.stage == 'init':
        if not args.source or not args.legacy_book:
            parser.error('init needs source and legacy-book')
        print(json.dumps(init_book(args.source, args.legacy_book, directory, set(parse_chapters(args.exclude))), ensure_ascii=False))
        return
    manifest = read(directory / 'rebuild/manifest.json')
    source_stat = Path(manifest['source']).stat()
    if manifest['source_stat'] != [source_stat.st_mtime_ns, source_stat.st_size]:
        raise ValueError('source changed since rebuild initialization')
    scope = manifest['planning_scope'] if args.stage in {'plan', 'review'} else manifest['facts_scope']
    chapters = scope if args.chapters == 'all' else sorted(set(parse_chapters(args.chapters)) & set(manifest['facts_scope']))
    if not chapters:
        parser.error('no selected chapters')
    with (directory / f'rebuild/{args.stage}.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.stage == 'compile':
            print(json.dumps(compile_book(directory, chapters), ensure_ascii=False))
        else:
            run_batch(directory, chapters, args.stage, args.workers)


if __name__ == '__main__':
    main()
