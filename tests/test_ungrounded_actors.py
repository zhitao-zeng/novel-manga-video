"""What to do with an actor whose name the chapter does not contain where the reading said it does.

Three chapters of 在美漫当心灵导师的日子 were lost to this, in two different ways, and the second was
hiding behind the first.  马特 really is in chapter 38 - at paragraph 6, while the reading cited 7 -
and the repair call, asked to fix its own numbering, returned the same number.  红乌鸦帮的老大 is not
in chapter 26 at all: it is a phrase the reading composed for someone the chapter only mentions.

One is a citation error, which the text itself can settle.  The other is an actor with no evidence,
and what to do about it depends entirely on whether the chapter puts them in the picture.
"""
from __future__ import annotations

import pytest

from novel_manga.story.source_identity import clean_reading

SEGMENTS = [{"segment_id": "seg_1", "text": "席勒站在讲台后，把一摞随测卷码齐。"},
            {"segment_id": "seg_2", "text": "而布鲁斯觉得自己和哈维特别投缘，他们什么都能聊到一起。"},
            {"segment_id": "seg_3", "text": "马特摘下墨镜，夜魔侠的名字在哥谭并不响亮。"}]


def actor(name, forms, *, source_id=1, presence="on_stage"):
    return {"source_id": source_id, "name": name, "kind": "individual", "presence": presence,
            "appearance": "", "paragraphs": [1],
            "forms": [{"form": f, "kind": "proper", "paragraphs": [p]} for f, p in forms]}


def test_a_form_the_chapter_has_elsewhere_is_reassigned_not_rejected():
    """The reading said paragraph 2 and the word is in paragraph 3.  Asking the model to renumber its
    own citation returned the same number twice and cost the chapter."""
    cleaned, rejected, unresolved = clean_reading({"actors": [actor("马特", [("马特", 2)])]}, SEGMENTS)
    assert not rejected and not unresolved
    form = cleaned["actors"][0]["forms"][0]
    assert form["paragraphs"] == [3]
    assert form["cited"] == [2]          # what the reading said, kept on the record


def test_a_correctly_cited_form_is_left_exactly_as_it_was():
    cleaned, rejected, _ = clean_reading({"actors": [actor("马特", [("马特", 3)])]}, SEGMENTS)
    assert cleaned["actors"][0]["forms"][0] == {"form": "马特", "kind": "proper", "paragraphs": [3]}
    assert not rejected


def test_several_forms_of_one_actor_are_each_placed_where_they_occur():
    cleaned, _, _ = clean_reading(
        {"actors": [actor("马特", [("马特", 1), ("夜魔侠", 1)])]}, SEGMENTS)
    assert [f["paragraphs"] for f in cleaned["actors"][0]["forms"]] == [[3], [3]]


def test_a_form_that_is_nowhere_in_the_chapter_is_still_rejected():
    """红乌鸦帮的老大 is a phrase the reading composed, not one the chapter uses."""
    cleaned, rejected, unresolved = clean_reading(
        {"actors": [actor("红乌鸦帮的老大", [("红乌鸦帮的老大", 1)], presence="mentioned")]}, SEGMENTS)
    assert cleaned["actors"] == []
    assert [r["form"] for r in rejected] == ["红乌鸦帮的老大"]
    assert [r["name"] for r in unresolved] == ["红乌鸦帮的老大"]


def test_an_actor_keeps_the_forms_that_ground_and_loses_the_ones_that_do_not():
    cleaned, rejected, unresolved = clean_reading(
        {"actors": [actor("马特", [("马特", 1), ("编出来的称呼", 1)])]}, SEGMENTS)
    assert [f["form"] for f in cleaned["actors"][0]["forms"]] == ["马特"]
    assert [r["form"] for r in rejected] == ["编出来的称呼"]
    assert not unresolved               # one grounded form is enough to keep the actor
