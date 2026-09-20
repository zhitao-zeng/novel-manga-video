"""Which of the bible's locations a chapter may be planned in.

Two rules meet here.  A place the story has not reached must not be offered, or a bible a thousand
chapters ahead lets chapter three film in a palace nobody has seen.  And a place the book already
established has to stay available, or a chapter that travels somewhere it names only in passing has
nowhere to go and every shot piles into whatever the last episode used - 超品相师 chapter 3 arrives at
a bus station and rides past a building site, and both were filmed in front of a shopping mall.
"""
from __future__ import annotations

from typing import Callable

BASE_CHAPTER = 0  # a location the base bible carried belongs to the world from the first chapter


def recently_used(group: dict, chapter: int, window: int) -> set[str]:
    """Names this group records for the `window` chapters before `chapter`."""
    return {name for name, chapters in group.items()
            if any(chapter - window <= int(c) < chapter for c in chapters)}


def short_of(full: str) -> str:
    """A bible location reads 名字：描写; the planner and the binder match on the name."""
    return full.split("：", 1)[0].strip()


def offered_locations(locations: list[str], *, chapter: int, named_here: Callable[[str], bool],
                      recent: set[str], added_at: dict[str, int], window: int) -> list[str]:
    """The bible locations this chapter may be planned in.

    A location with no bible_growth record came from the base bible, which is built from the opening
    chapters, so it counts as reached from the start - which is how the `known` filter below already
    reads it, and reading it as "never introduced" is what dropped it.
    """
    known = [full for full in locations if added_at.get(short_of(full), BASE_CHAPTER) <= chapter]
    offered = [full for full in known
               if named_here(short_of(full))
               or short_of(full) in recent
               or added_at.get(short_of(full), BASE_CHAPTER) == BASE_CHAPTER
               or chapter - window <= added_at[short_of(full)] <= chapter]
    if offered:
        return offered
    # Nothing named and nothing recent: fall back to the places introduced most recently BEFORE this
    # chapter, not the newest ones in a bible that may be far ahead of it.
    return sorted(known, key=lambda full: -added_at.get(short_of(full), BASE_CHAPTER))[:6] or locations[:6]
