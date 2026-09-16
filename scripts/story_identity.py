"""Shared, book-scoped identity data and source-grounded chapter bindings.

Names and design cards are retrieval candidates. A chapter reading determines
who the text refers to; aliases, bodies and appearances are different relations.
All consumers read the same saved result instead of adding word-specific rules.
"""
from __future__ import annotations

from pathlib import Path
import json
import re
import time

from novel_manga.runtime_backends import normalize_text
from novel_manga.story.identity import canonical_entity, resolved_aliases
from novel_manga.util import atomic_write_json

POLICY = 'story-identity-v5-grounded-types'
RELATIONS = ['same_as', 'rename', 'avatar_of', 'occupies_body', 'transformation', 'lookalike', 'impersonates']




def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def data_files(novel):
    novel = Path(novel).resolve()
    return [novel / name for name in ['story_bible.json', 'bible_aliases.json', 'entity_index.json',
            'series_assets/phases.json', 'entity/entities.json', 'entity/claims.json', 'entity/types.json']]


def data_signature(novel):
    return {str(p): [p.stat().st_mtime_ns, p.stat().st_size] if p.is_file() else None for p in data_files(novel)}


class IdentityCatalog:
    def __init__(self, novel: Path):
        self.novel = Path(novel).resolve()
        self.bible = read(self.novel / 'story_bible.json', {})
        ledger = read(self.novel / 'entity/entities.json', [])
        by_name = {r['canonical']: r for r in ledger}
        by_id = {r['id']: r for r in ledger}
        original_names = {}
        self.entities = {}
        for i, character in enumerate(self.bible.get('characters', []), 1):
            name = character['name']
            record = by_name.get(name, {})
            seen = set()
            while record.get('merged_into') in by_id and record['id'] not in seen:
                seen.add(record['id']); record = by_id[record['merged_into']]
            eid = record.get('id', f'e{i:03d}')
            original_names[name] = eid
            if eid not in self.entities or record.get('canonical') == name:
                forms = self.entities.get(eid, {}).get('forms', set()) | {name}
                self.entities[eid] = {'id': eid, 'name': name, 'asset_id': record.get('asset_id') or f'character_{i:03d}',
                                      'design': character, 'forms': forms}
            else:
                self.entities[eid]['forms'].add(name)
        self.entities['voice:collective'] = {'id': 'voice:collective', 'name': '无名群声', 'asset_id': None,
            'design': {'name': '无名群声', 'role': '原文群体共同发言的画外声，不绑定单个人物外貌'}, 'forms': {'无名群声'}}
        self.by_name = {r['name']: r for r in self.entities.values()}
        self.by_name.update({name: self.entities[eid] for name, eid in original_names.items()})
        self.aliases = {str(a): str(b) for a, b in read(self.novel / 'bible_aliases.json', {}).items()}
        for row in read(self.novel / 'entity_index.json', {}).get('characters', []):
            if row['name'] in self.by_name:
                self.by_name[row['name']]['forms'].update(row.get('forms') or {})
        # The index is not a replacement for the alias file. Both are retained
        # as candidates, including aliases added after the index was built.
        for alias, name in self.aliases.items():
            if name in self.by_name:
                self.by_name[name]['forms'].add(alias)
        self.claims = read(self.novel / 'entity/claims.json', [])
        self.phases = read(self.novel / 'series_assets/phases.json', {}).get('characters', {})

    def candidates(self, passage: str, names=()):
        text = normalize_text(passage)
        wanted = set(names)
        wanted.update(r['name'] for r in self.entities.values() if r['design'].get('role') in {'主角','男主角','女主角'})
        rows = [r for r in self.entities.values() if r['name'] in wanted
                or any(normalize_text(form) and normalize_text(form) in text for form in r['forms'])]
        rows.sort(key=lambda r: (r['name'] not in wanted,
                  -max((len(normalize_text(f)) for f in r['forms'] if normalize_text(f) in text), default=0)))
        return rows[:80]  # retrieval budget, not a rule that a shorter name cannot be a person

    def prompt_rows(self, passage: str, chapter: int, names=()):
        from thin_phases import phase_for
        rows = []
        for r in self.candidates(passage, names):
            c = r['design']
            rows.append({'entity_id': r['id'], 'name': r['name'], 'known_forms': sorted(r['forms']),
                         'design_reference_only': {k: str(c.get(k, ''))[:120] for k in ['role', 'gender', 'age', 'appearance']},
                         'appearance_phase': phase_for(self.phases, r['name'], chapter)})
        return rows


def chapter_inputs(directory):
    directory = Path(directory).resolve()
    return {'data': data_signature(directory.parent), 'segments': read(directory / 'segments.json', [])}


def source_schema(paragraphs):
    from thin_review import obj
    refs = {'type': 'array', 'minItems': 1, 'items': {'type': 'integer', 'minimum': 1, 'maximum': paragraphs}}
    sid = {'type': 'integer', 'minimum': 1, 'maximum': 40}
    return obj({
        'source_readable': {'type': 'boolean'}, 'source_problem': {'type': 'string', 'maxLength': 200},
        'actors': {'type': 'array', 'maxItems': 30, 'items': obj({
            'paragraphs': refs, 'name': {'type': 'string', 'maxLength': 40},
            'forms': {'type': 'array', 'maxItems': 8, 'items': obj({
                'form': {'type': 'string'}, 'kind': {'type': 'string', 'enum': ['proper','contextual']}, 'paragraphs': refs})},
            'kind': {'type': 'string', 'enum': ['individual','group','object']},
            'count': {'type': 'integer', 'minimum': 0, 'maximum': 100000},
            'presence': {'type': 'string', 'enum': ['on_stage','voice','mentioned']},
            'appearance': {'type': 'string', 'maxLength': 180}, 'source_id': sid})},
    })


class UnreadableSource(ValueError):
    pass


def usable_reading(context):
    """An empty extraction is not evidence that a chapter has no actors."""
    return bool(context.get('source_actors') or context.get('mentions') or context.get('actorless_confirmed'))


def active_cast_names(context):
    unresolved = [r['name'] for r in context.get('unmatched_actors', [])
                  if r.get('presence') in {'on_stage', 'voice'} and r.get('kind') == 'individual']
    if unresolved:
        raise ValueError('source actors need catalogue bindings: ' + ', '.join(unresolved))
    return {context['entities'].get(canonical_entity(context, m['entity_id']))
            for m in context.get('mentions', [])
            if m.get('presence') in {'on_stage', 'voice'} and m['entity_id'] != 'UNKNOWN'}


def clean_reading(answer, segments):
    """Discard unsupported alternate forms; never silently invent an actor binding."""
    actors, rejected, unresolved = [], [], []
    for source in answer['actors']:
        actor = {**source, 'forms': []}
        for form in source['forms']:
            ps = form.get('paragraphs', [])
            valid = ps and all(type(p) is int and 1 <= p <= len(segments) for p in ps)
            quote = '\n'.join(segments[p-1]['text'] for p in range(min(ps), max(ps)+1)) if valid else ''
            if normalize_text(form['form']) and normalize_text(form['form']) in normalize_text(quote):
                actor['forms'].append(form)
            else:
                rejected.append({'source_id': source['source_id'], **form})
        if not actor['forms']:
            unresolved.append(source)
        else:
            actors.append(actor)
    return {'actors': actors}, rejected, unresolved


def map_source_reading(answer, catalog, segments):
    # The raw source is read before any catalogue is offered to the model.
    # Catalogue matching cannot change the source's actor count or relations.
    actors = answer['actors']
    by_source = {r['source_id']: r for r in actors}
    if len(by_source) != len(actors):
        raise ValueError('source reading repeated an actor id')
    mapping = {}
    for actor in actors:
        forms = {actor['name'], *(f['form'] for f in actor['forms'] if f['kind'] == 'proper')}
        exact = {catalog.by_name[name]['id'] for name in forms if name in catalog.by_name}
        primary = [eid for eid in exact if catalog.entities[eid]['design'].get('role') in {'主角','男主角','女主角'}]
        if primary:
            eid = sorted(primary)[0]
        elif actor['name'] in catalog.by_name:
            eid = catalog.by_name[actor['name']]['id']
        elif len(exact) == 1:
            eid = next(iter(exact))
        else:
            matches = {r['id'] for r in catalog.entities.values() if r['forms'] & forms}
            eid = next(iter(matches)) if len(matches) == 1 else 'UNKNOWN'
        mapping[actor['source_id']] = eid
    # Distinct source actors do not collapse merely because a legacy alias
    # sends both to one catalogue row. Preserve the direct name and leave the
    # other actor unbound until its own identity/body asset can be established.
    by_entity = {}
    for actor in actors:
        eid = mapping[actor['source_id']]
        if eid != 'UNKNOWN':
            by_entity.setdefault(eid, []).append(actor)
    for eid, owners in by_entity.items():
        if len(owners) < 2:
            continue
        direct = [r for r in owners if r['name'] == catalog.entities[eid]['name']]
        keep = direct[0]['source_id'] if len(direct) == 1 else None
        for actor in owners:
            if actor['source_id'] != keep:
                mapping[actor['source_id']] = 'UNKNOWN'
    mentions, appearances = [], []
    for actor in actors:
        eid = mapping[actor['source_id']]
        mentions.extend({**f, 'entity_id': eid, 'presence': actor['presence'], 'source_actor': actor['source_id'],
                         'entity_kind': actor['kind'], 'count': actor.get('count', 0)}
                        for f in actor['forms'])
        if actor['appearance']:
            appearances.append({'entity_id': eid, 'description': actor['appearance'], 'paragraphs': actor['paragraphs']})
    relations = []
    grounded = ground({'mentions': mentions, 'relations': relations, 'appearances': appearances}, catalog, segments)
    grounded['source_actors'] = actors
    grounded['unmatched_actors'] = [r for r in actors if mapping[r['source_id']] == 'UNKNOWN']
    return grounded


def ground(answer, catalog, segments):
    texts = [s['text'] for s in segments]
    offsets, cursor = [], 0
    for text in texts:
        offsets.append(cursor); cursor += len(text) + 1
    ids = set(catalog.entities)
    result = {}
    for key in ['mentions', 'relations', 'appearances']:
        rows = []
        for row in answer[key]:
            ps = row.get('paragraphs', [])
            if not ps or any(type(p) is not int or not 1 <= p <= len(texts) for p in ps):
                raise ValueError('identity evidence refers to an absent source paragraph')
            fields = ['subject', 'object'] if key == 'relations' else ['entity_id']
            if any(row.get(field) not in ids | {'UNKNOWN'} for field in fields):
                raise ValueError('identity result refers to an absent entity id')
            first, last = min(ps) - 1, max(ps) - 1
            quote = '\n'.join(texts[first:last+1])
            if key == 'mentions' and (not normalize_text(row['form']) or normalize_text(row['form']) not in normalize_text(quote)):
                raise ValueError('identity mention does not occur in the supplied source evidence')
            rows.append({**row, 'source_quote': quote, 'source_span': [offsets[first], offsets[last]+len(texts[last])]})
        result[key] = rows
    return result


def resolve_chapter(directory: Path, *, force=False):
    # Read source first; legacy dictionaries never enter this semantic call.
    from thin_review import ask_json
    directory = Path(directory).resolve()
    expected = chapter_inputs(directory)
    saved = read(directory / 'identity_context.json', {})
    if not force and saved.get('policy') == POLICY and saved.get('inputs') == expected and usable_reading(saved):
        return saved
    segments = expected['segments']
    if not segments:
        raise ValueError('identity resolution needs source segments')
    chapter = int(directory.name.rsplit('_', 1)[1])
    catalog = IdentityCatalog(directory.parent)
    prompt = (
        '只读下面小说原文，独立列出本章实际指称的主体，不读取旧人物库、不改剧本、不评价画面。'
        '每个主体一个source_id。name选择原文主要叫法；同一人的旧名、新名和称谓记入同一actors条目的forms，不能因残留改名而拆人。'
        '根据连续动作、对话问答、受事对象和后续反馈判断是否同一人；不要求原文专门解释一次改名。'
        '分身和本体即使共享灵魂或肉身，只要各自行动、对话或同时出场，就列为两个主体，不能把双方名字放进同一个条目。'
        'forms只收原文实际使用的名字/称谓，不自己制造缩写；proper是专指此主体的叫法，contextual是局部代词或代称。'
        '先判断原文是否能连贯阅读；只有字序严重乱序、无法确定连续事件时source_readable=false，actors留空，不猜测还原。'
        '人名或数字中间换行、区段从半句话开始、错别字及旧名残留都是排版或身份核对问题；叙事仍连贯就必须填source_readable=true。'
        '代词不是独立人物。天气、境界、法术、抽象概念不列为人物；短字也可能是真正名字，依据语境判断。'
        'kind区分独立个体individual、群体group、具体道具object；对事件有作用的命名道具也列出，但绝不能当人物。'
        '动物、坐骑和被召唤的生物仍是individual；不能因为被召唤、被乘坐或沉默就当作object。只有原文明确为器物才填object，含混时保留individual。'
        '群体称号即使像姓名也不能拆成单一人物；count填原文明示的群体数量，不明填0，不根据旧人物库猜。'
        'presence区分在场、画外发言和仅被提及。'
        'appearance只写原文明示的当时身体/物种/外貌，没写就留空；不得自行补成人形或兽形。'
        '所有paragraphs均引用下列原文编号，由程序摘取证据；不要输出推理过程。\n'
        + json.dumps([{'paragraph': i, **r} for i, r in enumerate(segments, 1)], ensure_ascii=False))
    if (not force and saved.get('policy') == POLICY and saved.get('inputs', {}).get('segments') == segments
            and 'source_actors' in saved and usable_reading(saved)):
        answer = {'actors': saved['source_actors'], 'actorless_confirmed': saved.get('actorless_confirmed', False)}
    else:
        answer = ask_json([{'type': 'text', 'text': prompt}], source_schema(len(segments)),
                          name='chapter_identity_source', max_tokens=4096, timeout=240)
    raw_path = directory / 'identity_source_reading.json'
    atomic_write_json(raw_path, {'policy': POLICY, 'inputs': expected, 'answer': answer})
    if answer.get('source_readable') is False:
        from thin_review import obj
        confirmation = ask_json([{'type': 'text', 'text':
            '只判断这章小说的事件是否还能读懂，不做人物绑定，也不要求文本排版完美。'
            '人名、数字被换行切开，区段接续半句话，旧名和改名混用，均不构成原文不可读；'
            '只有字符顺序严重打乱、无法理解连续事件才填false。\n'
            + json.dumps(segments, ensure_ascii=False)}],
            obj({'source_readable': {'type':'boolean'}, 'source_problem': {'type':'string','maxLength':200}}),
            name='source_readability_confirmation', max_tokens=500, timeout=120)
        atomic_write_json(directory / 'source_readability_confirmation.json', confirmation)
        if not confirmation['source_readable']:
            raise UnreadableSource(confirmation.get('source_problem') or 'source text is unreadable')
        answer = ask_json([{'type':'text','text':prompt + '\n原文可读性已独立复核通过；按连续事件识别主体，不要把断行或旧名残留报成字序损坏。'}],
                          source_schema(len(segments)), name='chapter_identity_readable_source', max_tokens=4096, timeout=240)
        atomic_write_json(raw_path, {'policy': POLICY, 'inputs': expected, 'answer': answer})
        if answer.get('source_readable') is False:
            raise ValueError('identity extraction failed after source readability was confirmed')
    if not answer.get('actors') and not answer.get('actorless_confirmed'):
        schema = source_schema(len(segments))
        schema['properties']['actorless_confirmed'] = {'type': 'boolean'}
        schema['required'].append('actorless_confirmed')
        answer = ask_json([{'type': 'text', 'text': prompt + '\n上一次抽取返回空主体，需独立复核。'
            '逐段检查动作发起者、对话双方和提及对象；只要有人物或动物参与，就重新完整抽取actors。'
            '只有全文确实没有任何主体（例如纯景物描写），才允许actors为空并填actorless_confirmed=true。'
            '断行、人名残留或无法完成抽取不能作为无人章节；此时填false。'}], schema,
            name='chapter_identity_empty_confirmation', max_tokens=4096, timeout=240)
        atomic_write_json(directory / 'identity_empty_confirmation.json', {'policy': POLICY, 'inputs': expected, 'answer': answer})
        if answer.get('source_readable') is False or (not answer.get('actors') and not answer.get('actorless_confirmed')):
            raise ValueError('identity extraction returned no actors without confirming an actorless chapter')
    cleaned, rejected, unresolved = clean_reading(answer, segments)
    if unresolved:
        correction = ask_json([{'type': 'text', 'text': prompt + '\n只修正下列主体的forms与原文编号；保留source_id，不输出其他主体。'
            '每个主体必须至少有一个原文实际存在的指称，不能补出原文没有的代词。\n' + json.dumps(unresolved, ensure_ascii=False)}],
            source_schema(len(segments)), name='chapter_identity_evidence', max_tokens=2500, timeout=180)
        fixed, dropped, pending = clean_reading(correction, segments)
        wanted = {r['source_id'] for r in unresolved}
        restored = [r for r in fixed['actors'] if r['source_id'] in wanted]
        atomic_write_json(directory / 'identity_evidence_repair.json', {'answer': correction, 'rejected': dropped})
        if pending or {r['source_id'] for r in restored} != wanted:
            raise ValueError('identity actors lack grounded names: ' + ','.join(r['name'] for r in unresolved))
        cleaned['actors'].extend(restored)
        rejected.extend(dropped)
    result = {'policy': POLICY, 'chapter': chapter, 'at': time.strftime('%F %T'), 'inputs': expected,
              'primary_entities': [r['id'] for r in catalog.entities.values() if r['design'].get('role') in {'主角','男主角','女主角'}],
              'entities': {r['id']: r['name'] for r in catalog.entities.values()},
              'actorless_confirmed': not cleaned['actors'] and bool(answer.get('actorless_confirmed')),
              'discarded_mentions': rejected, **map_source_reading(cleaned, catalog, segments)}
    result['relations'] = [r for r in catalog.claims if r.get('status') == 'accepted' and r.get('chapter', 0) <= chapter]
    for key in ['mentions','appearances']:
        for row in result[key]:
            row['entity_id'] = canonical_entity(result, row['entity_id'])
    atomic_write_json(directory / 'identity_context.json', result)
    return result


def current_context(directory):
    result = read(Path(directory) / 'identity_context.json', {})
    return result if result.get('policy') == POLICY and usable_reading(result) and result.get('inputs') == chapter_inputs(Path(directory)) else {}


def typed_entities(novel, context=None):
    result = {}
    context = context or {}
    for m in context.get('mentions', []):
        name = context.get('entities', {}).get(canonical_entity(context, m['entity_id']))
        if name and m.get('entity_kind') == 'group':
            result[name] = {'kind': m['entity_kind'], 'count': m.get('count', 0),
                            'source_quote': m.get('source_quote', '')}
    # A model's tentative object label cannot erase an established creature's
    # card (summoned mounts can be misread as objects). Removing such a card
    # requires a source-verified book type correction. Group roles are scoped
    # to the chapter and retain their artwork as clothing references.
    result.update(read(Path(novel) / 'entity/types.json', {}))
    return result


def effective_aliases(novel, chapter=None, context=None):
    """Legacy names remain candidate data; a chapter's sourced reading wins."""
    catalog = IdentityCatalog(novel)
    context = context or (current_context(Path(novel) / f'{Path(novel).name}_{chapter}') if chapter is not None else {})
    return resolved_aliases(context, catalog.aliases)



def identity_rows(names, bible, passage, context=None, catalog=None):
    """Source bindings use evidence, not title suffixes or name-length rules."""
    context = context or {}
    wanted = set(names)
    rows = []
    characters = list(bible.get('characters', []))
    if '无名群声' in wanted and not any(c['name'] == '无名群声' for c in characters):
        characters.append({'name': '无名群声', 'role': '原文中的群体画外音'})
    for c in characters:
        name = c['name']
        if name not in wanted:
            continue
        entity = catalog.by_name.get(name, {}) if catalog else {}
        forms = set(entity.get('forms', {name}))
        evidence = [m for m in context.get('mentions', []) if context.get('entities', {}).get(canonical_entity(context,m['entity_id'])) == name
                    and m.get('presence') not in {'not_entity', 'uncertain'}]
        if context:
            source_names = list(dict.fromkeys(m['form'] for m in evidence))
        else:
            source_names = [f for f in forms if normalize_text(f) and normalize_text(f) in normalize_text(passage)]
        rows.append({'name': name, 'entity_id': entity.get('id'),
                     'source_names': source_names, 'source_mentions': evidence,
                     'candidate_names': sorted(forms)})
    return rows


def prompt_context(directory, names=(), *, context=None):
    directory = Path(directory).resolve()
    context = context if context is not None else current_context(directory)
    catalog = IdentityCatalog(directory.parent)
    chapter = int(directory.name.rsplit('_', 1)[1])
    segments = read(directory / 'segments.json', [])
    passage = '\n'.join(s['text'] for s in segments)
    candidates = catalog.prompt_rows(passage, chapter, names)
    if context:
        source_ids = {canonical_entity(context, m['entity_id']) for m in context.get('mentions', [])
                      if m['entity_id'] != 'UNKNOWN' and m.get('presence') not in {'not_entity','uncertain'}}
        candidates = [r for r in candidates if r['entity_id'] in source_ids]
        for candidate in candidates:
            candidate['known_forms'] = sorted({candidate['name'], *(m['form'] for m in context.get('mentions', [])
                if canonical_entity(context, m['entity_id']) == candidate['entity_id'] and m.get('kind') == 'proper')})
    selected = {r['name'] for r in candidates}
    ids = {r['entity_id'] for r in candidates}
    claims = [c for c in catalog.claims if c.get('status') == 'accepted' and c.get('chapter', 0) <= chapter
              and (c.get('subject') in ids or c.get('object') in ids)]
    return {'policy': POLICY, 'chapter': chapter,
            'candidates_not_source_facts': candidates,
            'legacy_alias_candidates': {} if context else {a:b for a,b in catalog.aliases.items() if b in selected},
            'resolved_name_aliases': effective_aliases(directory.parent, chapter, context) if context else {},
            'chapter_reading': {k: [{field:value for field,value in row.items() if field!='source_quote'}
                                    for row in context.get(k, [])] for k in ['mentions', 'relations', 'appearances']},
            'unmatched_source_actors': context.get('unmatched_actors', []),
            'entity_types': typed_entities(directory.parent, context),
            'source_paragraphs': reading_segments(directory, context) if context else [], 'accepted_ledger_relations': claims,
            'rules': '这是本章资料，不是每段必出场名单；当前镜头按对应原文和事件确定谁在场。所有名字以原文归属为准；艺术卡是画风参考。分身、身体和外观阶段不能当普通别名合并。'}


def prompt_block(directory, names=()):
    return '\n统一人物与形态资料（各环节共用）：\n' + json.dumps(prompt_context(directory, names), ensure_ascii=False) + '\n'


def reading_segments(directory, context=None):
    """Annotate identity links for reading; source files and quotes stay raw."""
    directory = Path(directory).resolve()
    context = context if context is not None else current_context(directory)
    aliases = effective_aliases(directory.parent, int(directory.name.rsplit('_', 1)[1]), context)
    observed = {r['form'] for r in context.get('mentions', []) if r.get('kind') == 'proper'
                and r.get('entity_id') != 'UNKNOWN'}
    aliases = {a:b for a,b in aliases.items() if a in observed}
    result = []
    for segment in read(directory / 'segments.json', []):
        text = segment['text']
        if aliases:
            # One substitution pass prevents another alias from rewriting the
            # annotation just inserted, including names with shared prefixes.
            ordered = sorted(aliases, key=len, reverse=True)
            pattern = '|'.join('(' + r'\s*'.join(re.escape(c) for c in a) + ')' for a in ordered)
            annotated = set()
            def annotate(match):
                alias = ordered[match.lastindex - 1]
                if alias in annotated:
                    return match[0]
                annotated.add(alias)
                return match[0] + f'〔身份注：用于人物指称时即{aliases[alias]}，同一实体〕'
            text = re.sub(pattern, annotate, text)
        result.append({**segment, 'text': text})
    return result
