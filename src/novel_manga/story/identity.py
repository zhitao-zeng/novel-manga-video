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


def scan_mentions(text: str, forms_by_name: dict[str, list[str]]) -> list[tuple[int, str, str]]:
    """(position, name, form) for every mention in the text, longest form first at each position and
    never overlapping: 赫尔曼 is 赫尔曼, not 赫尔男爵's 赫尔; 莱恩·诺克斯·格雷 is one mention, not three."""
    forms = sorted(((form, name) for name, own in forms_by_name.items() for form in own if form), key=lambda fn: -len(fn[0]))
    if not forms:
        return []
    pattern = re.compile("|".join(re.escape(form) for form, _ in forms))
    owner = {form: name for form, name in forms}
    return [(m.start(), owner[m.group(0)], m.group(0)) for m in pattern.finditer(text)]


def unique_forms(forms):
    owners = {}
    for name, own in forms.items():
        for form in own:
            owners.setdefault(form, set()).add(name)
    return {name: sorted([form for form in own if form == name or owners[form] == {name}], key=len, reverse=True)
            for name, own in forms.items()}


TITLE_SUFFIXES = ("公主", "殿下", "女士", "先生", "小姐", "夫人", "伯爵", "侯爵", "公爵", "男爵", "爵士", "王子", "国王", "王后",
                  "陛下", "大人", "修女", "神父", "主教", "婆婆", "船长", "医生", "教授", "老师", "队长", "警长", "侦探", "管家")

def short_forms(name: str) -> set[str]:
    """How the prose refers to a bible character besides the full name: the given name before a
    ·surname (琥珀·高德 → 琥珀), the name without its title (薇奥拉公主 → 薇奥拉), and 小 plus either
    (小琥珀).  Nothing shorter than two characters, so 船长 or 神 never gain a form.  Two characters
    sharing a form are both offered; the model picks."""
    base = str(name).strip()
    forms: set[str] = set()
    if "·" in base:
        given = base.split("·", 1)[0].strip()
        if len(given) >= 2:
            forms.add(given)
    for suffix in TITLE_SUFFIXES:
        if base.endswith(suffix) and len(base) - len(suffix) >= 2:
            forms.add(base[: -len(suffix)])
    for form in list(forms):
        if not form.startswith("小"):
            forms.add("小" + form)
    forms.discard(base)
    return forms

def name_forms_for(name, indexed, aliases):
    if name in indexed:
        return {name, *indexed[name], *(a for a, target in aliases.items() if target == name)}
    return {name, *(a for a, target in aliases.items() if target == name), *short_forms(name)}


def mentioned_names(text, names, indexed, aliases, generic):
    usable = unique_forms({n: {f for f in name_forms_for(n, indexed, aliases) if len(f) >= 2} for n in names})
    eligible = {n: usable.get(n, []) for n in names if len(n) >= 2
                and (not generic[n] if n in indexed and n in generic else len(n) >= 3 or '·' in n)}
    return list(dict.fromkeys(name for _, name, _ in scan_mentions(text, eligible)))
