"""Build planning/review views from one explicit chapter identity snapshot."""
from __future__ import annotations
import novel_manga.episodes as ep_names

from pathlib import Path
import json
import re
from novel_manga.story.identity import canonical_entity, resolved_aliases
from novel_manga.story.source_identity import POLICY
from novel_manga.application.identity.store import read, current_context, load_catalog, load_chapter
from novel_manga.application.identity.phases import phase_for

def prompt_rows(catalog, passage: str, chapter: int, names=()):
    rows = []
    for r in catalog.candidates(passage, names):
        c = r['design']
        rows.append({'entity_id': r['id'], 'name': r['name'], 'known_forms': sorted(r['forms']),
                     'design_reference_only': {k: str(c.get(k, ''))[:120] for k in ['role', 'gender', 'age', 'appearance']},
                     'appearance_phase': phase_for(catalog.phases, r['name'], chapter)})
    return rows


def typed_entities(novel, context=None, *, data=None):
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
    result.update(data.book.types if data is not None else read(Path(novel) / 'entity/types.json', {}))
    return result


def effective_aliases(novel, chapter=None, context=None, *, data=None):
    """Legacy names remain candidate data; a chapter's sourced reading wins."""
    catalog = data.catalog if data is not None else load_catalog(novel)
    context = context or (current_context(Path(novel) / f'{Path(novel).name}_{chapter}', data=data) if chapter is not None else {})
    return resolved_aliases(context, catalog.aliases)


def prompt_context(directory, names=(), *, context=None, data=None):
    directory = Path(directory).resolve()
    data = data if data is not None else load_chapter(directory)
    context = context if context is not None else data.context
    catalog = data.catalog
    chapter = ep_names.chapter_of(directory.name)
    segments = data.segments
    passage = '\n'.join(s['text'] for s in segments)
    candidates = prompt_rows(catalog, passage, chapter, names)
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
            'resolved_name_aliases': effective_aliases(directory.parent, chapter, context, data=data) if context else {},
            'chapter_reading': {k: [{field:value for field,value in row.items() if field!='source_quote'}
                                    for row in context.get(k, [])] for k in ['mentions', 'relations', 'appearances']},
            'unmatched_source_actors': context.get('unmatched_actors', []),
            'entity_types': typed_entities(directory.parent, context, data=data),
            'source_paragraphs': reading_segments(directory, context, data=data) if context else [], 'accepted_ledger_relations': claims,
            'rules': '这是本章资料，不是每段必出场名单；当前镜头按对应原文和事件确定谁在场。所有名字以原文归属为准；艺术卡是画风参考。分身、身体和外观阶段不能当普通别名合并。'}


def prompt_block(directory, names=(), *, data=None):
    return '\n统一人物与形态资料（各环节共用）：\n' + json.dumps(prompt_context(directory, names, data=data), ensure_ascii=False) + '\n'


def reading_segments(directory, context=None, *, data=None):
    """Annotate identity links for reading; source files and quotes stay raw."""
    directory = Path(directory).resolve()
    data = data if data is not None else load_chapter(directory)
    context = context if context is not None else data.context
    aliases = effective_aliases(directory.parent, ep_names.chapter_of(directory.name), context, data=data)
    observed = {r['form'] for r in context.get('mentions', []) if r.get('kind') == 'proper'
                and r.get('entity_id') != 'UNKNOWN'}
    aliases = {a:b for a,b in aliases.items() if a in observed}
    result = []
    for segment in data.segments:
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
