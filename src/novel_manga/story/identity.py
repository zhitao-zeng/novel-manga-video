"""Resolve only explicit identities; retrieval matches are not identity claims."""
from __future__ import annotations
import re


def canonical_name(value, aliases=None, extras=()):
    name = str(value or '').strip()
    return name if name in extras else (aliases or {}).get(name, name)


def canonical_entity(context, eid):
    group = {eid}
    links = [r for r in context.get('relations', []) if r['type'] in {'same_as','rename'}
             and 'UNKNOWN' not in (r['subject'],r['object'])]
    while True:
        before = set(group)
        for r in links:
            if r['subject'] in group or r['object'] in group:
                group.update([r['subject'],r['object']])
        if group == before:
            break
    preferred = [e for e in context.get('primary_entities', []) if e in group]
    return preferred[0] if preferred else min(group)


def name_matches(name: str, known: list[str]) -> bool:
    compact = re.sub(r"\s+", "", name)
    # Containment is useful for retrieval, not identity. A catalogue entry
    # named "吴" or "龙" used to prevent "吴龙" from ever being registered.
    # Existing aliases are resolved separately by the growth caller.
    return bool(compact) and any(compact == re.sub(r"\s+", "", k) for k in known)


def resolved_aliases(context, legacy_aliases):
    aliases = {} if context else dict(legacy_aliases)
    owners = {}
    for row in context.get('mentions', []):
        if row.get('presence') in {'not_entity', 'uncertain'} or row['entity_id'] == 'UNKNOWN' or row.get('kind') == 'contextual':
            aliases.pop(row['form'], None)
            continue
        owners.setdefault(row['form'], set()).add(canonical_entity(context,row['entity_id']))
    for form, ids in owners.items():
        if len(ids) == 1:
            name = context['entities'].get(next(iter(ids)))
            if name:
                aliases[form] = name
        else:
            aliases.pop(form, None)  # a title naming several people is not a global alias
    for relation in context.get('relations', []):
        if relation['type'] not in {'avatar_of', 'occupies_body', 'impersonates'}:
            continue
        names = {context.get('entities', {}).get(relation[side]) for side in ['subject', 'object']}
        for alias, target in list(aliases.items()):
            if alias in names and target in names:
                aliases.pop(alias)
    return {a:b for a,b in aliases.items() if a != b}
