"""A book is read to chapter 100 and then planned to chapter 300.

Every earlier book was taken in stages and none of them met this, because none of them had a roster:
在美漫当心灵导师的日子 is the first book built reading-first, and the night it went past chapter 100 was
the first time anything had been planned outside a reading's coverage.

reading_decisions states the rule - "outside it, absence means nothing at all" - and the planner's
half kept it.  The growth gate did not: it answered "not on the roster" with a refusal in chapters the
reading had never opened, so 罗伊·布朗, 蒂娜·布朗 and everyone else who arrives after chapter 100 was
turned away at the door and had to be argued back in downstream.  That path then failed without a
word: the fill's reasons were unpacked into `_` and dropped, and it was not told the reading vouched
for the people it was asked about.  Two of the first five chapters died on "source actors need
catalogue bindings" while a sibling chapter built the same person seconds later.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor

import pytest

from novel_manga.application.production.flow import Batch
from novel_manga.application.review import bible as review_bible
from novel_manga.util import atomic_write_json

READ_TO_100 = {"characters": ["席勒", "布鲁斯·韦恩"], "aliases": {"蝙蝠侠": "布鲁斯·韦恩"},
               "declined": ["黑人大块头", "黑衣人"], "chapters": list(range(1, 101))}


def row(name, mentions=2, kind="具名角色", speaks=False):
    return {"name": name, "mentions": mentions, "kind": kind, "speaks_or_close_up": speaks}


@pytest.fixture
def book(tmp_path, monkeypatch):
    novel = tmp_path / "book"
    novel.mkdir()
    atomic_write_json(novel / "story_bible.json", {
        "novel_title": "测试", "genre": "通用", "visual_style": "美漫", "palette": "冷蓝",
        "style_fingerprint": "test", "locations": ["庭院：空旷院落"],
        "characters": [{"name": "席勒", "role": "人物", "appearance": "黑发", "wardrobe": "外套"}]})
    atomic_write_json(novel / "bible_aliases.json", {})
    atomic_write_json(novel / "reading_cast.json", READ_TO_100)
    said = []
    monkeypatch.setattr(review_bible.model_client, "log", said.append)
    return novel, said


def grown(novel, monkeypatch, chapter, rows):
    """Which names one chapter of growth decided to ask the fill for."""
    asked = []
    def fill(bible_obj, bible_path, missing, text, cast=None):
        asked.extend(missing)
        return bible_obj, [], {}, {}
    monkeypatch.setattr(review_bible, "fill_characters", fill)
    review_bible._grow_bible_unlocked(novel, "原文", chapter, scan={"names": rows, "locations": []})
    return asked


def test_someone_who_arrives_after_the_reading_stopped_is_built_on_the_chapters_evidence(book, monkeypatch):
    novel, said = book
    assert grown(novel, monkeypatch, 102, [row("罗伊·布朗"), row("蒂娜·布朗")]) == ["罗伊·布朗", "蒂娜·布朗"]
    assert any("读书只读到第 100 章" in line for line in said)


def test_the_same_name_inside_the_reading_is_still_refused(book, monkeypatch):
    """The reading had chapter 50 in front of it and did not keep them: that is a ruling."""
    novel, said = book
    assert grown(novel, monkeypatch, 50, [row("罗伊·布朗")]) == []
    assert any("读书名单里没有" in line for line in said)


def test_past_the_reading_the_one_chapter_noise_filter_is_what_decides(book, monkeypatch):
    """Named once and silent: the rule every unread book has always used says no."""
    novel, _ = book
    assert grown(novel, monkeypatch, 102, [row("路过的警员", mentions=1)]) == []


def test_a_form_the_reading_declined_stays_declined_wherever_it_turns_up(book, monkeypatch):
    """黑人大块头 was seen across a hundred chapters and left out on purpose.  Meeting him again in
    chapter 103 is not new evidence that he is a character."""
    novel, said = book
    assert grown(novel, monkeypatch, 103, [row("黑人大块头", speaks=True)]) == []
    assert any("读书看过并否决的指称" in line for line in said)


def test_someone_on_the_roster_is_built_past_the_reading_too(book, monkeypatch):
    novel, _ = book
    assert grown(novel, monkeypatch, 150, [row("布鲁斯·韦恩", mentions=1)]) == ["布鲁斯·韦恩"]


def test_a_reading_that_recorded_no_coverage_is_taken_at_its_word_everywhere(book, monkeypatch):
    """"How far it read" cannot be assumed to be "not this far"."""
    novel, _ = book
    atomic_write_json(novel / "reading_cast.json", {k: v for k, v in READ_TO_100.items() if k != "chapters"})
    assert grown(novel, monkeypatch, 102, [row("罗伊·布朗")]) == []


def on_stage(name):
    return {"name": name, "presence": "on_stage", "kind": "individual"}


def test_the_rescue_tells_the_fill_that_the_reading_vouched_for_them(book, monkeypatch):
    """Without it "疑为某某的别称" refuses the very people this function exists to build."""
    novel, _ = book
    told = {}
    def fill(bible_obj, bible_path, missing, text, cast=None):
        told["cast"] = cast
        return bible_obj, [], {}, {}
    monkeypatch.setattr(review_bible, "fill_characters", fill)
    review_bible.grow_unbound_roster(novel, {"unmatched_actors": [on_stage("布鲁斯·韦恩")]}, "原文", 102)
    assert told["cast"] == ["席勒", "布鲁斯·韦恩"]


def test_a_rescue_that_did_not_take_says_why(book, monkeypatch):
    """补建 was logged twice above the traceback and neither line said why nothing was built."""
    novel, said = book
    def fill(bible_obj, bible_path, missing, text, cast=None):
        return bible_obj, [], {"布鲁斯·韦恩": "把握不足（0.4）"}, {}
    monkeypatch.setattr(review_bible, "fill_characters", fill)
    assert review_bible.grow_unbound_roster(novel, {"unmatched_actors": [on_stage("布鲁斯·韦恩")]}, "原文", 102) == []
    assert any("补建没建成" in line and "把握不足（0.4）" in line for line in said)


def test_a_rescue_that_took_has_nothing_to_explain(book, monkeypatch):
    novel, said = book
    def fill(bible_obj, bible_path, missing, text, cast=None):
        return bible_obj, list(missing), {}, {}
    monkeypatch.setattr(review_bible, "fill_characters", fill)
    review_bible.grow_unbound_roster(novel, {"unmatched_actors": [on_stage("布鲁斯·韦恩")]}, "原文", 102)
    assert not any("补建没建成" in line for line in said)


class SecondPass(Batch):
    """The batch with planning replaced by a script of outcomes, one list per chapter."""

    def __init__(self, outcomes, dry_run=False):
        self.args = argparse.Namespace(dry_run=dry_run)
        self.rows = {chapter: {} for chapter in outcomes}
        self.outcomes, self.planned = {c: list(o) for c, o in outcomes.items()}, []

    def plan(self, chapter, **_):
        self.planned.append(chapter)
        self.rows[chapter]["plan"] = self.outcomes[chapter].pop(0)


def second_pass(batch, chapters):
    for chapter in chapters:
        batch.plan(chapter)
    batch.planned.clear()
    with ThreadPoolExecutor(max_workers=3) as planners:
        batch.second_pass(chapters, planners)
    return sorted(batch.planned)


def test_what_failed_is_planned_once_more_after_the_rest_of_the_batch(monkeypatch):
    """102 asked for 罗伊·布朗 before 103 had built him; run again, it planned without a change."""
    monkeypatch.setattr("novel_manga.application.production.common.log", lambda *a, **k: None)
    batch = SecondPass({101: ["planned"], 102: ["failed", "planned"], 103: ["planned"], 104: ["failed", "planned"]})
    assert second_pass(batch, [101, 102, 103, 104]) == [102, 104]
    assert [batch.rows[c]["plan"] for c in (102, 104)] == ["planned", "planned"]


def test_a_chapter_that_fails_twice_is_left_failed_not_tried_until_it_works(monkeypatch):
    monkeypatch.setattr("novel_manga.application.production.common.log", lambda *a, **k: None)
    batch = SecondPass({102: ["failed", "failed", "planned"]})
    assert second_pass(batch, [102]) == [102]
    assert batch.rows[102]["plan"] == "failed"


def test_a_batch_with_nothing_failed_plans_nothing_twice(monkeypatch):
    monkeypatch.setattr("novel_manga.application.production.common.log", lambda *a, **k: None)
    batch = SecondPass({101: ["planned"], 102: ["kept"], 103: ["skipped (too short)"]})
    assert second_pass(batch, [101, 102, 103]) == []
