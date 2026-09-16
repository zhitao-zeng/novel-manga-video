"""planning.cast responsibilities, extracted without changing requests or policy."""
from __future__ import annotations
from novel_manga.planning.context import PlannerContext
from novel_manga.story.identity import canonical_name
from novel_manga.story.identity import name_forms_for
from novel_manga.story.identity import scan_mentions
from novel_manga.story.identity import unique_forms


def canonical(name: str, *, ctx: PlannerContext) -> str:
    return canonical_name(name, ctx.aliases)


def name_forms(name: str, *, ctx: PlannerContext) -> set[str]:
    """Every string that names this character: from the entity index when the book has one, else the name,
    its aliases from bible_aliases.json and its derived short forms."""
    return name_forms_for(name, ctx.entity_forms, ctx.aliases)


def _usable_forms(everyone: tuple[str, ...], *, ctx: PlannerContext) -> dict[str, list[str]]:
    """name -> the strings that point at that character and nobody else.  A form two characters share
    (约翰 for 约翰·华生 and 约翰·邓恩教授) or that sits inside another character's name (赫尔 in 赫尔曼)
    would make the prose add the wrong person, so it is dropped; the full name always stays.  Indexed
    once per cast list and alias table."""
    key = (everyone, len(ctx.aliases), len(ctx.entity_forms))
    if key in ctx.forms_index:
        return ctx.forms_index[key]
    forms = {name: {f for f in name_forms(name, ctx=ctx) if len(f) >= 2} for name in everyone}
    usable = unique_forms(forms)
    ctx.forms_index[key] = usable
    return usable


def mentioned_characters(text: str, everyone: list[str], *, ctx: PlannerContext) -> list[str]:
    """Bible characters a piece of prose names, in order of first mention.  A two-character name with
    neither surname nor title (灵魂, 秘女, 船长, 天使) is a common noun as often as a person and is left
    to the model - 761's cat was the price of trusting the model alone with everyone else."""
    usable = _usable_forms(tuple(everyone), ctx=ctx)
    eligible = {name: usable.get(name, []) for name in everyone if len(name) >= 2
                and (not ctx.entity_generic[name] if name in ctx.entity_forms and name in ctx.entity_generic
                     else len(name) >= 3 or "·" in name)}
    seen: list[str] = []
    for _, name, _ in scan_mentions(text, eligible):
        if name not in seen:
            seen.append(name)
    return seen


def complete_characters(characters: list[str], shot: dict, everyone: list[str], cap: int = 6, *, ctx: PlannerContext) -> tuple[list[str], list[str]]:
    """The shot's cast plus every character its own description puts on camera.  2026-09-13, 雾月 761:
    the description said 薇奥拉 kissed 莱恩, the enum had no 薇奥拉, the cast was [莱恩, 琥珀] - and the
    cat did the kissing.  Returns the cast and what was added."""
    described = mentioned_characters(f"{shot.get('visual_prompt') or ''}\n{shot.get('motion_prompt') or ''}", everyone, ctx=ctx)
    added = [name for name in described if name not in characters][: max(0, cap - len(characters))]
    return list(characters) + added, added
