"""Existing book scanning, bounded bible growth and volume recap workflow."""
from __future__ import annotations

import json
from pathlib import Path




def reading_character_facts(novel_dir: Path) -> dict:
    """The same confirmed reading facts used by setup, without redesigning existing people."""
    path = Path(novel_dir) / "reading_cast.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("character_facts") or {}


def reading_decisions(novel_dir: Path) -> dict:
    """What a whole-book reading settled: its cast, the names it saw and declined, how far it read.

    From inside one chapter three different situations look identical, and only two of them are
    decisions: somebody the reading made a character of, somebody it saw across the book and left out
    on purpose, and somebody it never had in front of it at all.  Planning may play the second as an
    extra.  The third has to stop the chapter - otherwise a character the reading missed is quietly
    demoted to a walk-on, which is the exact defect the binding check exists to catch.

    `chapters` is the reading's coverage, taken from the chapter numbers its own candidate table cites.
    Inside it, a form the reading never promoted to a character is a decision about that form.  Outside
    it, absence means nothing at all.
    """
    empty = {"roster": [], "cast": [], "declined": [], "chapters": []}
    path = Path(novel_dir) / "reading_cast.json"
    if not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    names = [str(n).strip() for n in data.get("characters") or [] if str(n).strip()]
    names += [str(a).strip() for a in (data.get("aliases") or {}) if str(a).strip()]
    return {"roster": names,
            # the characters alone: an alias key is legitimately an alias, a character is not
            "cast": [str(n).strip() for n in data.get("characters") or [] if str(n).strip()],
            "declined": [str(n).strip() for n in data.get("declined") or [] if str(n).strip()],
            "chapters": [int(c) for c in data.get("chapters") or [] if str(c).isdigit()]}


def reading_roster(novel_dir: Path) -> list[str]:
    """Everyone the lean reading decided this book has, names and aliases alike.

    Empty when the book has not been read, and then growth keeps its own judgement.
    """
    return reading_decisions(novel_dir)["roster"]


def decided_extras(reading: dict, actors, chapter: int, forms_of) -> tuple[list[str], list[str]]:
    """Sort the actors this chapter could not bind into (extras, unjudged).

    `forms_of(name)` yields the name forms to match against the reading.  Someone the reading kept is
    never an extra: they have to bind, and if they cannot, that is the defect the binding check exists
    for.  Someone it saw and declined is an extra, on its own evidence.

    Everyone else is neither.  "The reading read this chapter" used to stand in for the second case,
    and it does not: a whole-book candidate table keeps the forms that recur, so a person named once
    never reaches it whether they matter or not.  Reading a chapter is not the same as ruling on
    everyone in it, and treating the silence as a verdict is how a character the reading missed
    becomes a walk-on.  They are returned separately so the caller can get a verdict rather than
    assume one.
    """
    roster, declined = set(reading.get("roster") or ()), set(reading.get("declined") or ())
    extras, unjudged = [], []
    for actor in actors:
        forms = set(forms_of(actor["name"]))
        if forms & roster:
            continue                       # a character the reading kept: must bind, never an extra
        (extras if forms & declined else unjudged).append(actor["name"])
    return extras, unjudged


def load_aliases(novel_dir: Path) -> dict:
    path = novel_dir / "bible_aliases.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
