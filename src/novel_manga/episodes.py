"""What an episode directory is called, and what its name says.

    <novel>_12       chapter 12, one episode
    <novel>_12-2     the second episode of chapter 12, when the chapter was cut into several

Forty places read the chapter number back out of the name with int(name.rsplit("_", 1)[1]).  Each
of them broke, or worse silently skipped the directory, the first time a chapter became three
episodes - "skipped" meaning the H3 prompt builder left the part without an English prompt and the
delivery report never listed it.  They read it from here now, and a sort by episode_order puts a
chapter's parts after the chapter before and in their own order.
"""
from __future__ import annotations

import re

NAME = re.compile(r"^(?P<novel>.+)_(?P<chapter>\d+)(?:-(?P<part>\d+))?$")


def episode_name(novel_id: str, chapter: int, part: int | None = None) -> str:
    return f"{novel_id}_{chapter}" + (f"-{part}" if part else "")


def parse_episode(name: str) -> tuple[int, int | None] | None:
    """(chapter, part) from a directory name, or None for a name that is not an episode's."""
    found = NAME.match(str(name).strip())
    if not found:
        return None
    return int(found.group("chapter")), (int(found.group("part")) if found.group("part") else None)


def is_episode(name: str) -> bool:
    return parse_episode(name) is not None


def chapter_of(name: str) -> int:
    """The chapter an episode directory belongs to; every part of chapter 12 answers 12."""
    parsed = parse_episode(name)
    if parsed is None:
        raise ValueError(f"not an episode directory name: {name!r}")
    return parsed[0]


def part_of(name: str) -> int | None:
    parsed = parse_episode(name)
    return parsed[1] if parsed else None


def episode_order(name: str) -> tuple[int, int]:
    """A sort key: chapters in order, a chapter's parts in order after the chapter before them."""
    parsed = parse_episode(name)
    if parsed is None:
        return (10 ** 9, 0)
    chapter, part = parsed
    return (chapter, part or 0)
