"""The task pack an agent is actually handed.

write_brief had no test at all, and it showed: a template placeholder went missing and three full
suite runs said nothing, because everything that exercises the sandbox path stubs this function out.
A pack that cannot be written is a chapter that cannot be attempted, so it is checked here by being
written.

The numbers in it are the other half.  The brief used to carry its own budget - about 100 seconds,
10-20 shots, 4-15 s each - while the gate computes target and ceiling from planning.budget and the
per-shot cap depends on which lane renders the book.  An author writing to one set and judged by the
other loses chapters to arithmetic, which is no verdict on the writing.
"""
from __future__ import annotations

import json

import pytest

from novel_manga.planning.authored_brief import (COLUMNS, episode_budget, run_prompt, style_word,
                                                 task_note, write_brief)

NOVEL = {"title": "测试书", "chapters": [{"index": 1, "title": "第1章 开端"},
                                         {"index": 2, "title": "第2章 再遇"}]}
BIBLE = {"novel_title": "测试书", "visual_style": "统一美漫风", "palette": "冷蓝",
         "characters": [{"name": "席勒", "role": "主角", "age": "三十岁", "appearance": "黑发",
                         "wardrobe": "长风衣"}],
         "locations": ["诊室：白墙与旧木桌"]}


@pytest.fixture
def book(tmp_path):
    novel = tmp_path / "book"
    novel.mkdir()
    source = novel / "source.md"
    source.write_text("\n".join(
        line for c in NOVEL["chapters"]
        for line in (c["title"], f"席勒走进诊室，第 {c['index']} 章的事情就这样开始了。" * 8, "")),
        encoding="utf-8")
    (novel / "novel.json").write_text(json.dumps(
        {**NOVEL, "source": str(source)}, ensure_ascii=False), encoding="utf-8")
    (novel / "story_bible.json").write_text(json.dumps(BIBLE, ensure_ascii=False), encoding="utf-8")
    (novel / "profile.json").write_text(json.dumps({"style": "meiman", "frame": "16:9"}), encoding="utf-8")
    (novel / "cast_index.json").write_text(json.dumps(
        {"characters": {"席勒": [1, 2]}, "locations": {"诊室": [1, 2]}}, ensure_ascii=False), encoding="utf-8")
    return novel


def test_the_pack_is_written_and_holds_the_three_files_the_prompt_names(book, tmp_path):
    written = write_brief(book, 2, tmp_path / "input", "shanyin")
    assert {path.name for path in written} == {"任务说明.md", "人物地点与画风.md", "source.txt", "prompt.txt"}
    assert all(path.stat().st_size for path in written)
    # the three input files go in input/; the prompt that drives the run sits beside it, because it is
    # what starts the agent rather than something the agent reads
    assert {p.name for p in (tmp_path / "input").iterdir()} == {"任务说明.md", "人物地点与画风.md", "source.txt"}
    prompt = (tmp_path / "prompt.txt").read_text(encoding="utf-8")
    assert prompt == run_prompt("shanyin", json.loads((book / "novel.json").read_text(encoding="utf-8")), 2)
    for name in ("任务说明.md", "人物地点与画风.md", "source.txt"):
        assert name in prompt


def test_the_nine_column_header_survives_into_the_pack(book, tmp_path):
    """The columns are matched by name downstream; a pack that renames or drops one is refused."""
    write_brief(book, 1, tmp_path / "input", "shanyin")
    assert COLUMNS in (tmp_path / "input" / "任务说明.md").read_text(encoding="utf-8")


def test_the_budget_in_the_pack_is_the_budget_the_gate_will_use(book, tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_CLIP_SECONDS_MAX", "15")
    write_brief(book, 1, tmp_path / "input", "shanyin")
    note = (tmp_path / "input" / "任务说明.md").read_text(encoding="utf-8")
    budget = episode_budget("原文" * 100, {"tier": "quality"})
    # the target is what the planner aims at, the ceiling what it may not cross; 15 seconds apart
    # was close enough that a dense chapter hit the wall on its first draft
    assert budget["ceiling"] == 120 and budget["target"] == 90
    assert f"不超过 {budget['ceiling']:g} 秒" in note
    assert f"{budget['spoken_low']}–{budget['spoken_high']} 字" in note


def test_the_per_shot_cap_follows_the_lane_that_will_render_it(monkeypatch):
    """15 seconds on a local-H3 lane, 30 otherwise.  A brief built on the default invites shots the
    renderer cannot make."""
    monkeypatch.setenv("NOVEL_CLIP_SECONDS_MAX", "15")
    assert episode_budget("原文", {"tier": "quality"})["shot_high"] == 15
    monkeypatch.setenv("NOVEL_CLIP_SECONDS_MAX", "30")
    assert episode_budget("原文", {"tier": "quality"})["shot_high"] == 30


def test_the_style_is_named_the_way_its_package_names_it(book, tmp_path):
    write_brief(book, 1, tmp_path / "input", "shanyin")
    assert "美漫" in (tmp_path / "input" / "任务说明.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("key, expected", [("meiman", "美漫"), ("weimei", "唯美"), ("live", "真人"),
                                           ("3d", "3D国漫"), ("2d", "二维国漫")])
def test_every_style_package_has_a_word_for_itself(key, expected):
    assert style_word(key) == expected


def test_an_unknown_style_is_passed_through_rather_than_renamed():
    assert style_word("没有这个风格") == "没有这个风格"


def test_the_first_episode_and_a_later_one_are_briefed_differently(book):
    budget = episode_budget("原文", {"tier": "quality"})
    novel = json.loads((book / "novel.json").read_text(encoding="utf-8"))
    first = task_note(novel, 1, [], {"style": "meiman", "frame": "16:9"}, budget, book)
    later = task_note(novel, 2, [{"chapter": 1, "summary": "席勒到了哥谭"}],
                      {"style": "meiman", "frame": "16:9"}, budget, book)
    assert "这是系列第一集" in first and "前情" not in first
    assert "不要重新介绍他" in later and "席勒到了哥谭" in later


def test_an_unknown_skill_is_refused_before_a_container_is_started():
    with pytest.raises(ValueError, match="不认识的 skill"):
        run_prompt("没这套技能", NOVEL, 1)
