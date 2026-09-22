"""Forty places read the chapter out of an episode directory's name with int(name.rsplit("_", 1)[1]).

The first time a chapter became three episodes each of them broke, or worse skipped the directory
in silence: the H3 prompt builder's ".isdigit()" guard left every part without an English prompt,
and the delivery report never listed one.  They all read the name through one parser now.
"""
from __future__ import annotations

import pytest

from novel_manga import episodes as ep


@pytest.mark.parametrize("name, chapter, part", [
    ("meiman-daoshi_12", 12, None), ("meiman-daoshi_12-1", 12, 1), ("meiman-daoshi_12-3", 12, 3),
    ("wuyue_1597", 1597, None), ("a_b_7-2", 7, 2),
])
def test_a_name_says_its_chapter_and_its_part(name, chapter, part):
    assert ep.parse_episode(name) == (chapter, part)
    assert ep.chapter_of(name) == chapter and ep.part_of(name) == part
    assert ep.is_episode(name)


@pytest.mark.parametrize("name", ["series_assets", "meiman-daoshi_", "meiman-daoshi_x", "lean", "meiman-daoshi_12-", ".inflight"])
def test_a_name_that_is_not_an_episodes_is_told_apart(name):
    assert ep.parse_episode(name) is None and not ep.is_episode(name)
    with pytest.raises(ValueError):
        ep.chapter_of(name)


def test_a_chapters_parts_sort_after_the_chapter_before_and_in_their_own_order():
    names = ["m_13", "m_12-3", "m_12-1", "m_11", "m_12-2", "series_assets"]
    assert sorted(names, key=ep.episode_order) == ["m_11", "m_12-1", "m_12-2", "m_12-3", "m_13", "series_assets"]


def test_the_name_is_built_and_read_by_the_same_rule():
    for chapter, part in ((12, None), (12, 2), (300, 3)):
        assert ep.parse_episode(ep.episode_name("meiman-daoshi", chapter, part)) == (chapter, part)
