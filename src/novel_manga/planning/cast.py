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


PRESENCE_FIELDS = ('visual_prompt', 'motion_prompt', 'end_state')


def presence_candidates(characters: list[str], shot: dict, everyone: list[str], *, ctx: PlannerContext) -> dict[str, list[dict]]:
    """Who the shot's own text names, and where - each mention tagged by what it is evidence of.

    Two grades, because one rule for both invented ghosts and lost actors in the same week:

      on_camera    the name appears in the picture the stage paints (开始时/事件/结束时 tableau):
                   someone standing, moving or being acted on there.  This is what the 761 defect
                   was about (the description said 薇奥拉 kissed 莱恩 and the cat did the kissing).
      talked_about the name appears only in spoken lines (or nowhere the picture paints it): 席勒
                   mentions 佩珀 in a threat, 托尼 worries about 贾维斯.  A line of dialogue is not
                   a camera; a name in it is a candidate for a later shot, never presence here.

    Returns {name: [{'field': ..., 'grade': 'on_camera'|'talked_about'}, ...]}; `characters` are
    not included - they are already in the cast.
    """
    picture = "\n".join(str(shot.get(field) or '') for field in PRESENCE_FIELDS)
    spoken = "\n".join(str(turn.get('text') or '') for turn in (shot.get('turns') or []))
    on_camera = set(mentioned_characters(picture, everyone, ctx=ctx))
    talked = set(mentioned_characters(spoken, everyone, ctx=ctx)) - on_camera
    out: dict[str, list[dict]] = {}
    for name in sorted(on_camera | talked):
        if name in characters:
            continue
        evidence = []
        for field in PRESENCE_FIELDS:
            if name in mentioned_characters(str(shot.get(field) or ''), everyone, ctx=ctx) and name in on_camera:
                evidence.append({'field': field, 'grade': 'on_camera'})
        if name in talked:
            evidence.append({'field': 'turns', 'grade': 'talked_about'})
        out[name] = evidence
    return out


def complete_characters(characters: list[str], shot: dict, everyone: list[str], cap: int = 6, *, ctx: PlannerContext) -> tuple[list[str], list[str]]:
    """The shot's cast plus every character its own description puts on camera.  2026-09-13, 雾月 761:
    the description said 薇奥拉 kissed 莱恩, the enum had no 薇奥拉, the cast was [莱恩, 琥珀] - and
    the cat did the kissing.  Returns the cast and what was added.

    Only the picture's own tableau adds people here (the on_camera grade of presence_candidates).
    A name the lines merely speak of - 佩珀 in a threat, 贾维斯 in a complaint - is a candidate
    recorded for the next shot, not a body in this one: giving it a card here is how 佩珀 walked
    into the clinic the moment the entity index learned her short name."""
    candidates = presence_candidates(characters, shot, everyone, ctx=ctx)
    added = [name for name, evidence in candidates.items()
             if any(e['grade'] == 'on_camera' for e in evidence)][: max(0, cap - len(characters))]
    return list(characters) + added, added
