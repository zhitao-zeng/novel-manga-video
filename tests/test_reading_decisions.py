"""What a whole-book reading decided, and what it merely never saw.

Three situations look identical from inside one chapter and only two of them are decisions:
somebody the reading made a character of, somebody it saw across the book and left out, and
somebody it never had in front of it.  Every test here is a chapter that used to be lost, or a
person who used to be lost, because two of the three were treated as one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_manga.application.identity.store import data_files, data_signature
from novel_manga.application.planning.initialize import reading_verdicts
from novel_manga.application.review.bible import decided_extras, reading_decisions, reading_roster
from novel_manga.story.source_identity import active_cast_names


def write_reading(novel: Path, **extra) -> None:
    novel.mkdir(parents=True, exist_ok=True)
    (novel / "reading_cast.json").write_text(json.dumps(
        {"characters": ["布鲁斯·韦恩", "阿尔弗雷德"], "aliases": {"蝙蝠侠": "布鲁斯·韦恩", "阿福": "阿尔弗雷德"},
         **extra}, ensure_ascii=False), encoding="utf-8")


def test_the_alias_file_is_part_of_the_identity_cache_key(tmp_path):
    """A new alias must re-bind the chapters that were saved without it.

    The binding consults these aliases, so leaving the file out of the signature meant adding the alias
    that proves 蝙蝠侠 and 布鲁斯 are one man changed nothing: every chapter already resolved kept its
    saved UNKNOWN and kept refusing to plan, at a defect that had just been fixed.
    """
    novel = tmp_path / "book"
    write_reading(novel)
    assert novel / "reading_cast.json" in data_files(novel)
    before = data_signature(novel)
    write_reading(novel, aliases={"蝙蝠侠": "布鲁斯·韦恩", "阿福": "阿尔弗雷德", "布鲁斯": "布鲁斯·韦恩"})
    assert data_signature(novel) != before


def test_reading_verdicts_separate_what_was_declined_from_what_was_never_read(tmp_path):
    novel = tmp_path / "book"
    (novel / "lean").mkdir(parents=True)
    (novel / "lean" / "candidates.json").write_text(json.dumps({"forms": [
        {"form": "布鲁斯·韦恩", "listed": [1, 2, 3]},
        {"form": "阿福", "listed": [2]},
        {"form": "乞丐", "listed": [1, 4]},
    ]}, ensure_ascii=False), encoding="utf-8")
    verdicts = reading_verdicts(novel, ["布鲁斯·韦恩"], {"阿福": "阿尔弗雷德"})
    assert verdicts["declined"] == ["乞丐"]        # seen across the book and not made a character
    assert verdicts["chapters"] == [1, 2, 3, 4]    # and this is how far the reading actually read


def test_reading_verdicts_stay_empty_without_the_candidate_table(tmp_path):
    """No table, no record of a decision - so later nobody may claim one was made."""
    novel = tmp_path / "book"
    novel.mkdir()
    assert reading_verdicts(novel, ["布鲁斯·韦恩"], {}) == {}


def test_a_name_the_reading_declined_plays_as_an_extra(tmp_path):
    novel = tmp_path / "book"
    write_reading(novel, declined=["乞丐"], chapters=[1, 2])
    reading = reading_decisions(novel)
    assert reading_roster(novel) == reading["roster"]
    assert decided_extras(reading, [{"name": "乞丐"}], chapter=9, forms_of=lambda n: {n}) == ["乞丐"]


def test_an_unnamed_passer_by_in_a_chapter_the_reading_read_is_an_extra(tmp_path):
    """前台的姑娘 never reaches the candidate table: one chapter uses her, the whole-book table keeps
    what recurs.  The reading DID read that chapter, and did not make her a character, so that is the
    verdict on her - and the chapter plans."""
    novel = tmp_path / "book"
    write_reading(novel, declined=["乞丐"], chapters=[1, 2])
    reading = reading_decisions(novel)
    assert decided_extras(reading, [{"name": "前台的姑娘"}], chapter=2, forms_of=lambda n: {n}) == ["前台的姑娘"]


def test_beyond_the_chapters_the_reading_read_nothing_is_waved_through(tmp_path):
    """The whole point of the separation: a reading of chapters 1-2 has no opinion about chapter 9.

    Treating its silence there as "extra" is how a character the reading never met becomes a walk-on.
    """
    novel = tmp_path / "book"
    write_reading(novel, declined=["乞丐"], chapters=[1, 2])
    reading = reading_decisions(novel)
    assert decided_extras(reading, [{"name": "新登场的关键人物"}], chapter=9, forms_of=lambda n: {n}) == []


def test_someone_the_reading_made_a_character_of_is_never_an_extra(tmp_path):
    """Even inside the covered chapters, and even under an alias: 阿福 has to bind to 阿尔弗雷德."""
    novel = tmp_path / "book"
    write_reading(novel, declined=[], chapters=[1, 2])
    reading = reading_decisions(novel)
    assert decided_extras(reading, [{"name": "阿福"}], chapter=1, forms_of=lambda n: {n}) == []


def test_the_binding_check_still_stops_a_chapter_for_an_actor_nobody_decided():
    context = {"unmatched_actors": [{"name": "新登场的关键人物", "presence": "on_stage", "kind": "individual"}],
               "mentions": [], "entities": {}}
    with pytest.raises(ValueError, match="新登场的关键人物"):
        active_cast_names(context)
    assert active_cast_names(context, ["新登场的关键人物"]) == set()


# --- who the per-chapter growth builds ----------------------------------------------------------

def growth_candidates(tmp_path, monkeypatch, rows, *, aliases=None, cast=None, bible=("席勒",)):
    """Run one chapter of bible growth and report which names it decided to build."""
    from novel_manga.application.review import bible as review_bible
    from novel_manga.util import atomic_write_json
    novel = tmp_path / "book"
    novel.mkdir(exist_ok=True)
    atomic_write_json(novel / "story_bible.json", {
        "novel_title": "测试", "genre": "通用", "visual_style": "美漫", "palette": "冷蓝",
        "style_fingerprint": "test", "locations": ["庭院：空旷院落"],
        "characters": [{"name": n, "role": "人物", "appearance": "黑发", "wardrobe": "外套"} for n in bible]})
    atomic_write_json(novel / "bible_aliases.json", aliases or {})
    if cast is not None:
        atomic_write_json(novel / "reading_cast.json", cast)
    built = []
    def fill(bible_obj, bible_path, missing, text):
        built.extend(missing)
        return bible_obj, [], {}, {}
    monkeypatch.setattr(review_bible, "fill_characters", fill)
    monkeypatch.setattr(review_bible.model_client, "log", lambda *a, **k: None)
    review_bible._grow_bible_unlocked(novel, "原文", 6, scan={"names": rows, "locations": []})
    return built


def row(name, mentions=2, kind="具名角色", speaks=False):
    return {"name": name, "mentions": mentions, "kind": kind, "speaks_or_close_up": speaks}


def test_someone_on_the_roster_is_built_from_a_single_silent_mention(tmp_path, monkeypatch):
    """暴风女 is named once in chapter 6 and says nothing, so the two-mentions rule declined her - and
    the binding check then refused the chapter, because she is on stage and binds to nothing.

    That rule is noise filtering for when one chapter is all you can see.  The reading has already
    filtered with the whole book, so applying it again only loses chapters.
    """
    built = growth_candidates(tmp_path, monkeypatch, [row("暴风女", mentions=1)],
                              cast={"characters": ["席勒", "暴风女"], "aliases": {}})
    assert built == ["暴风女"]


def test_a_name_the_reading_left_out_is_still_not_built(tmp_path, monkeypatch):
    """猎犬 is a hound and 鹰嘴门环 a door knocker; both are mentioned twice and get a close-up, which is
    all the one-chapter rule ever asked for.  The reading read the book and did not make them people."""
    built = growth_candidates(tmp_path, monkeypatch, [row("猎犬", speaks=True), row("鹰嘴门环", speaks=True)],
                              cast={"characters": ["席勒"], "aliases": {}})
    assert built == []


def test_a_chapter_that_only_uses_a_nickname_still_builds_the_person(tmp_path, monkeypatch):
    """The chapter says 阿福, the alias map knows that is 阿尔弗雷德, and the bible has neither.

    Aliases are skipped here so a nickname never becomes a second card - but that also meant nobody
    built the person, because this chapter never uses his own name.  He then failed to bind and the
    whole chapter was lost.
    """
    built = growth_candidates(tmp_path, monkeypatch, [row("阿福")],
                              aliases={"阿福": "阿尔弗雷德"},
                              cast={"characters": ["席勒", "阿尔弗雷德"], "aliases": {"阿福": "阿尔弗雷德"}})
    assert built == ["阿尔弗雷德"]


def test_a_nickname_of_someone_already_in_the_bible_stays_a_nickname(tmp_path, monkeypatch):
    built = growth_candidates(tmp_path, monkeypatch, [row("老席")],
                              aliases={"老席": "席勒"},
                              cast={"characters": ["席勒"], "aliases": {"老席": "席勒"}})
    assert built == []


# --- when the two readers of one chapter disagree ------------------------------------------------

def unbound_growth(tmp_path, monkeypatch, actors, *, aliases=None, cast=None, bible=("席勒",)):
    from novel_manga.application.review import bible as review_bible
    from novel_manga.util import atomic_write_json
    novel = tmp_path / "book"
    novel.mkdir(exist_ok=True)
    atomic_write_json(novel / "story_bible.json", {
        "novel_title": "测试", "genre": "通用", "visual_style": "美漫", "palette": "冷蓝",
        "style_fingerprint": "test", "locations": ["庭院：空旷院落"],
        "characters": [{"name": n, "role": "人物", "appearance": "黑发", "wardrobe": "外套"} for n in bible]})
    atomic_write_json(novel / "bible_aliases.json", aliases or {})
    atomic_write_json(novel / "reading_cast.json", cast or {"characters": [], "aliases": {}})
    asked = []
    def fill(bible_obj, bible_path, missing, text):
        asked.extend(missing)
        return bible_obj, list(missing), {}, {}
    monkeypatch.setattr(review_bible, "fill_characters", fill)
    monkeypatch.setattr(review_bible.model_client, "log", lambda *a, **k: None)
    review_bible.grow_unbound_roster(novel, {"unmatched_actors": actors}, "原文", 39)
    return asked


def on_stage(name):
    return {"name": name, "presence": "on_stage", "kind": "individual"}


def test_an_actor_the_name_scan_missed_is_built_from_the_chapters_own_reading(tmp_path, monkeypatch):
    """Chapter 39's name scan had no 猫女; its source reading had her on stage.

    The reading calls her a character, so she may not play as an extra, and the bible had nobody for
    her to bind to - the chapter could not be planned at all.  The side that knows exactly who is
    missing now asks for her, under the name she actually has.
    """
    asked = unbound_growth(tmp_path, monkeypatch, [on_stage("猫女")],
                           aliases={"猫女": "赛琳娜"},
                           cast={"characters": ["席勒", "赛琳娜"], "aliases": {"猫女": "赛琳娜"}})
    assert asked == ["赛琳娜"]


def test_an_actor_the_reading_never_vouched_for_is_not_built(tmp_path, monkeypatch):
    """Still the binding check's business, and its business is to stop rather than invent a character."""
    asked = unbound_growth(tmp_path, monkeypatch, [on_stage("前台的姑娘")],
                           cast={"characters": ["席勒"], "aliases": {}})
    assert asked == []


def test_someone_merely_mentioned_is_not_built(tmp_path, monkeypatch):
    asked = unbound_growth(tmp_path, monkeypatch,
                           [{"name": "赛琳娜", "presence": "mentioned", "kind": "individual"}],
                           cast={"characters": ["席勒", "赛琳娜"], "aliases": {}})
    assert asked == []


def test_nothing_is_built_for_a_book_that_was_never_read(tmp_path, monkeypatch):
    assert unbound_growth(tmp_path, monkeypatch, [on_stage("猫女")]) == []
