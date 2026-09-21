"""Source evidence and catalogue binding rules; no files or model requests."""
from __future__ import annotations

import re
import json
from novel_manga.runtime_backends import normalize_text
from novel_manga.story.identity import canonical_entity, resolved_aliases

POLICY = 'story-identity-v5-grounded-types'
RELATIONS = ['same_as', 'rename', 'avatar_of', 'occupies_body', 'transformation', 'lookalike', 'impersonates']

def source_schema(paragraphs):
    from novel_manga.llm.client import obj
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


def active_cast_names(context, extras_by_decision=()):
    """Who is on stage in this chapter, refusing to answer when someone cannot be accounted for.

    `extras_by_decision` are the people a reading of the whole book deliberately left out of the
    bible - a hound, a passer-by with two mentions.  They are accounted for: they play as extras,
    described in the shot, with an anonymous role for any line.  Anyone else who is on stage and
    binds to nothing is a person the film would drop or hand to the wrong character, and that stops
    the plan.
    """
    decided = set(extras_by_decision or ())
    unresolved = [r['name'] for r in context.get('unmatched_actors', [])
                  if r.get('presence') in {'on_stage', 'voice'} and r.get('kind') == 'individual'
                  and r['name'] not in decided]
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


def map_source_reading(answer, catalog, segments, verified_aliases=None):
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
    verified = verified_aliases or {}
    for eid, owners in by_entity.items():
        if len(owners) < 2:
            continue
        canonical = catalog.entities[eid]['name']
        # An alias the reading established - with the chapter and the line that prove it - says these
        # really are one person, which is the case the guard below must not undo.  A chapter that
        # only ever says 蝙蝠侠 and 布鲁斯 would otherwise leave both unbound and stop the plan.
        vouched = {r['source_id'] for r in owners
                   if r['name'] == canonical or verified.get(r['name']) == canonical}
        if vouched:
            for actor in owners:
                if actor['source_id'] not in vouched:
                    mapping[actor['source_id']] = 'UNKNOWN'
            continue
        direct = [r for r in owners if r['name'] == canonical]
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


