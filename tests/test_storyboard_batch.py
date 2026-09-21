"""Three hundred chapters cannot each wait for a person to type a path - and none may be chosen for them.

在美漫当心灵导师的日子 had its profile set to the sandbox backend after a pilot of one chapter, and
that made the arithmetic plain: ninety-nine chapters planned by the ordinary planner, one by the
agent, and every further chapter refused until somebody ran propose, looked, and ran accept.  The
batch does those errands.  These tests are about the line it must not cross - taking a take when
there was something to choose, or passing along a loss nobody was shown - and about the two ways a
long run goes wrong on its own: an allowance that changes at ten in the morning, and an endpoint that
goes away and takes the rest of the book with it.
"""
from __future__ import annotations

import json
import threading
import time
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from novel_manga.application.agents import storyboard as agent_storyboard
from novel_manga.application.agents import storyboard_batch as batch
from novel_manga.application.agents.sandbox import SandboxRefused, parallel_now
from novel_manga.planning.storyboard import HEADERS

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
P = "http://schemas.openxmlformats.org/package/2006/relationships"

SHOTS = [["1", "平视", "中景", "席勒站在讲台后，把一摞随测卷码齐。", "哥谭大学阶梯教室",
          "席勒（说、平静）：“今天随堂测验。”\n声音：翻纸声", "固定", "立住人物", "6"],
         ["2", "俯拍", "近景", "布鲁斯低头看卷子，笔尖停住。", "哥谭大学阶梯教室",
          "布鲁斯（内心独白）：“他是故意的。”", "缓推", "给出反应", "5"]]


def write_xlsx(path, worksheets, header=None):
    """A real workbook, because what is being tested is whether the real reader can read it."""
    book, rels = ET.Element(f"{{{S}}}workbook"), ET.Element(f"{{{P}}}Relationships")
    listed = ET.SubElement(book, f"{{{S}}}sheets")
    parts = {}
    for number, (name, shots) in enumerate(worksheets.items(), 1):
        ET.SubElement(listed, f"{{{S}}}sheet", {"name": name, "sheetId": str(number), f"{{{R}}}id": f"r{number}"})
        ET.SubElement(rels, f"{{{P}}}Relationship", {"Id": f"r{number}", "Target": f"worksheets/s{number}.xml"})
        sheet = ET.Element(f"{{{S}}}worksheet")
        data = ET.SubElement(sheet, f"{{{S}}}sheetData")
        for i, values in enumerate([list(header or HEADERS), *shots], 1):
            row = ET.SubElement(data, f"{{{S}}}row", {"r": str(i)})
            for j, value in enumerate(values):
                numeric = i > 1 and j == 8
                cell = ET.SubElement(row, f"{{{S}}}c", {"r": f"{chr(65 + j)}{i}", "t": "n" if numeric else "inlineStr"})
                if numeric:
                    ET.SubElement(cell, f"{{{S}}}v").text = value
                else:
                    ET.SubElement(ET.SubElement(cell, f"{{{S}}}is"), f"{{{S}}}t").text = value
        parts[f"xl/worksheets/s{number}.xml"] = sheet
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        for name, root in {"xl/workbook.xml": book, "xl/_rels/workbook.xml.rels": rels, **parts}.items():
            archive.writestr(name, ET.tostring(root, encoding="utf-8"))
    return path


@pytest.fixture
def book(tmp_path):
    novel = tmp_path / "book"
    novel.mkdir()
    return novel, {"runs_root": str(tmp_path / "runs"), "parallel": {"day": 2, "night": 4}}


def candidate(novel, config, chapter, *files, worksheets=None):
    """What propose leaves behind: files under the run's output/ and a candidate record."""
    episode = batch.episode_dir(novel, chapter)
    run = agent_storyboard.run_name(novel.name, chapter, "shanyin")
    for name in files:
        target = tmp_runs(config) / run / "output" / name
        if name.endswith(".xlsx"):
            write_xlsx(target, worksheets or {f"第{chapter}集": SHOTS})
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("notes", encoding="utf-8")
    agent_storyboard.write_state(episode, agent_storyboard.StoryboardState(
        status="candidate", run=run, attempt="20260922-040000-shanyin", skill="shanyin",
        sheets=[f"output/{name}" for name in files if name.endswith(".xlsx")]))
    return episode


def tmp_runs(config):
    from pathlib import Path
    return Path(config["runs_root"])


# ---- the line it must not cross -------------------------------------------------------------------

def test_one_readable_sheet_is_taken_and_the_record_says_a_rule_took_it(book):
    novel, config = book
    episode = candidate(novel, config, 3, "分镜表.xlsx")
    accepted, reason = agent_storyboard.auto_accept(episode, config=config)
    assert reason == ""
    assert accepted.status == "accepted" and accepted.sheet_name == "第3集"
    assert accepted.accepted_by == f"auto:{agent_storyboard.AUTO_RULE}"
    assert (episode / accepted.sheet).is_file()
    assert agent_storyboard.accepted_sheet(episode, config=config)[1] == "第3集"


def test_two_takes_are_a_choice_and_nobody_makes_it_for_them(book):
    novel, config = book
    episode = candidate(novel, config, 3, "分镜表.xlsx", "分镜表_v2.xlsx")
    current, reason = agent_storyboard.auto_accept(episode, config=config)
    assert current.status == "candidate"
    assert "2 份" in reason and "分镜表_v2.xlsx" in reason


def test_a_workbook_holding_two_complete_storyboards_is_the_same_choice_one_level_down(book):
    novel, config = book
    episode = candidate(novel, config, 3, "分镜表.xlsx", worksheets={"导演稿": SHOTS, "精简稿": SHOTS[:1]})
    current, reason = agent_storyboard.auto_accept(episode, config=config)
    assert current.status == "candidate" and "导演稿" in reason and "精简稿" in reason


def test_a_line_that_does_not_parse_is_a_line_that_would_not_be_filmed(book):
    """云烨：不可能。 has no （说）, so the reader drops it - silently, unless somebody is told here."""
    novel, config = book
    lossy = [[*SHOTS[0][:5], "席勒：今天随堂测验。", *SHOTS[0][6:]], SHOTS[1]]
    episode = candidate(novel, config, 3, "分镜表.xlsx", worksheets={"第3集": lossy})
    current, reason = agent_storyboard.auto_accept(episode, config=config)
    assert current.status == "candidate"
    assert "镜 1" in reason and "席勒：今天随堂测验。" in reason and "会丢" in reason


def test_an_attempt_that_wrote_no_sheet_is_said_to_have_written_none(book):
    novel, config = book
    episode = candidate(novel, config, 3, "notes.md")
    assert agent_storyboard.auto_accept(episode, config=config)[1] == "这次尝试没有产出分镜表"


def test_a_file_that_is_not_a_workbook_is_reported_not_raised(book):
    novel, config = book
    episode = candidate(novel, config, 3, "分镜表.xlsx")
    (tmp_runs(config) / agent_storyboard.run_name("book", 3, "shanyin") / "output" / "分镜表.xlsx").write_bytes(b"not a zip")
    current, reason = agent_storyboard.auto_accept(episode, config=config)
    assert current.status == "candidate" and reason.startswith("分镜表读不了")


def test_a_sheet_missing_a_column_is_reported_with_the_readers_own_words(book):
    """The agent dropped 预算秒: the reader already says so by name, and that sentence is the report."""
    novel, config = book
    episode = candidate(novel, config, 3, "分镜表.xlsx")
    path = tmp_runs(config) / agent_storyboard.run_name("book", 3, "shanyin") / "output" / "分镜表.xlsx"
    write_xlsx(path, {"第3集": [row[:-1] for row in SHOTS]}, header=list(HEADERS)[:-1])
    current, reason = agent_storyboard.auto_accept(episode, config=config)
    assert current.status == "candidate"
    assert "缺少列" in reason and "预算秒" in reason


def test_what_a_person_chose_is_left_exactly_as_they_chose_it(book):
    novel, config = book
    episode = candidate(novel, config, 3, "分镜表.xlsx")
    agent_storyboard.accept(episode, "output/分镜表.xlsx", sheet_name="第3集", config=config)
    before = agent_storyboard.state_path(episode).read_text(encoding="utf-8")
    accepted, reason = agent_storyboard.auto_accept(episode, config=config)
    assert reason == "" and accepted.accepted_by == "person"
    assert agent_storyboard.state_path(episode).read_text(encoding="utf-8") == before


# ---- one chapter ----------------------------------------------------------------------------------

class World:
    """The four steps that touch something real, replaced by a record of what was asked of them."""

    def __init__(self, novel, config, *, bind_ok=True, clean=True, up=True, busy=(), produce=("分镜表.xlsx",)):
        self.novel, self.config, self.calls = novel, config, []
        self.bind_ok, self.clean, self.up, self.busy_chapters, self.produce = bind_ok, clean, up, set(busy), produce
        self.slept, self.said, self.lock = [], [], threading.Lock()

    def steps(self):
        return batch.Steps(propose=self.propose, bind=self.bind, trace=self.trace,
                           endpoint_up=self.endpoint_up, busy=lambda c: c in self.busy_chapters,
                           sleep=self.slept.append, log=self.said.append)

    def note(self, what, chapter):
        with self.lock:
            self.calls.append((what, chapter))

    def propose(self, chapter):
        self.note("propose", chapter)
        candidate(self.novel, self.config, chapter, *self.produce)

    def bind(self, chapter):
        self.note("bind", chapter)
        if self.bind_ok:
            (batch.episode_dir(self.novel, chapter) / "clip_plan.json").write_text(
                json.dumps({"clips": [{}, {}, {}]}), encoding="utf-8")
        return self.bind_ok, "" if self.bind_ok else "source actors need catalogue bindings: 罗伊"

    def trace(self, chapter):
        self.note("trace", chapter)
        return self.clean, "" if self.clean else "镜 4 的时长 原表 6 秒 → 剧本 9 秒"

    def endpoint_up(self):
        return self.up() if callable(self.up) else self.up


def test_a_chapter_nobody_has_touched_goes_all_the_way_and_in_order(book):
    novel, config = book
    world = World(novel, config)
    result = batch.run_chapter(novel, 7, world.steps(), config=config)
    assert [what for what, _ in world.calls] == ["propose", "bind", "trace"]
    assert (result.outcome, result.clips, result.reason) == (batch.READY, 3, "")
    assert result.accepted_by.startswith("auto:")


def test_a_chapter_waiting_for_a_choice_is_not_asked_for_a_third_take(book):
    """Another quarter of an hour would produce another candidate a person still has to choose from."""
    novel, config = book
    candidate(novel, config, 7, "a.xlsx", "b.xlsx")
    world = World(novel, config)
    result = batch.run_chapter(novel, 7, world.steps(), config=config)
    assert world.calls == [] and result.outcome == batch.WAITING


def test_asked_to_it_writes_another_take_for_a_chapter_still_waiting(book):
    novel, config = book
    candidate(novel, config, 7, "notes.md")
    world = World(novel, config)
    result = batch.run_chapter(novel, 7, world.steps(), config=config, repropose=True)
    assert ("propose", 7) in world.calls and result.outcome == batch.READY


def test_a_chapter_already_accepted_and_bound_is_only_checked(book):
    novel, config = book
    episode = candidate(novel, config, 10, "分镜表.xlsx")
    agent_storyboard.accept(episode, "output/分镜表.xlsx", sheet_name="第10集", config=config)
    (episode / "clip_plan.json").write_text(json.dumps({"clips": [{}] * 9}), encoding="utf-8")
    world = World(novel, config)
    result = batch.run_chapter(novel, 10, world.steps(), config=config)
    assert world.calls == [("trace", 10)]
    assert (result.outcome, result.clips, result.accepted_by) == (batch.READY, 9, "person")


def test_a_plan_older_than_the_acceptance_is_somebody_elses_plan_and_is_made_again(book):
    """The ordinary planner's plan was sitting in all ninety-nine of them."""
    import os
    novel, config = book
    episode = batch.episode_dir(novel, 7)
    (episode / "clip_plan.json").write_text(json.dumps({"clips": [{}] * 5}), encoding="utf-8")
    os.utime(episode / "clip_plan.json", (time.time() - 3600,) * 2)
    candidate(novel, config, 7, "分镜表.xlsx")
    world = World(novel, config)
    batch.run_chapter(novel, 7, world.steps(), config=config)
    assert ("bind", 7) in world.calls


def test_a_binding_that_fails_is_reported_and_not_traced(book):
    novel, config = book
    world = World(novel, config, bind_ok=False)
    result = batch.run_chapter(novel, 7, world.steps(), config=config)
    assert result.outcome == batch.LOOK and "罗伊" in result.reason
    assert ("trace", 7) not in world.calls


def test_a_binding_that_drifted_from_its_sheet_needs_a_look_not_a_pass(book):
    novel, config = book
    result = batch.run_chapter(novel, 7, World(novel, config, clean=False).steps(), config=config)
    assert result.outcome == batch.LOOK and "镜 4" in result.reason


def test_a_container_still_running_from_last_time_is_left_alone(book):
    novel, config = book
    world = World(novel, config, busy=[7])
    result = batch.run_chapter(novel, 7, world.steps(), config=config)
    assert world.calls == [] and result.outcome == batch.FAILED and "还在跑" in result.reason


def test_a_sandbox_that_refuses_to_start_fails_the_chapter_with_its_reason(book):
    novel, config = book
    world = World(novel, config)
    def refuse(chapter):
        raise SandboxRefused("配置里没有这套技能：shanyin")
    steps = world.steps()
    steps.propose = refuse
    result = batch.run_chapter(novel, 7, steps, config=config)
    assert result.outcome == batch.FAILED and "配置里没有这套技能" in result.reason


# ---- a long run -----------------------------------------------------------------------------------

def test_no_more_run_at_once_than_the_hour_allows(book):
    novel, config = book
    world, peak, running = World(novel, config), [0], [0]
    inner = world.propose
    def slow(chapter):
        with world.lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.15)
        inner(chapter)
        with world.lock:
            running[0] -= 1
    steps = world.steps()
    steps.propose = slow
    results = batch.run_batch(novel, list(range(1, 9)), steps, config=config, allowance=lambda c: 2)
    assert peak[0] == 2 and len(results) == 8 and all(r.outcome == batch.READY for r in results)


def test_the_allowance_is_asked_again_as_the_run_goes_on(book):
    """A run this long starts under one allowance and ends under another."""
    novel, config = book
    asked = []
    def allowance(_):
        asked.append(1)
        return 4 if len(asked) < 3 else 2
    batch.run_batch(novel, list(range(1, 7)), World(novel, config).steps(), config=config, allowance=allowance)
    assert len(asked) > 3


def test_an_endpoint_that_is_down_holds_the_run_instead_of_failing_the_book(book):
    """Every chapter once failed in 0.004 seconds because the model was still loading."""
    novel, config = book
    answers = iter([False, False, True])
    world = World(novel, config, up=lambda: next(answers, True))
    results = batch.run_batch(novel, [1, 2], world.steps(), config=config, allowance=lambda c: 1)
    assert world.slept == [60.0, 60.0]
    assert [r.outcome for r in results] == [batch.READY, batch.READY]
    assert any("不会把章节记成失败" in line for line in world.said)


def test_one_chapter_blowing_up_does_not_end_the_book(book):
    novel, config = book
    world = World(novel, config)
    inner = world.bind
    def bind(chapter):
        if chapter == 2:
            raise RuntimeError("disk full")
        return inner(chapter)
    steps = world.steps()
    steps.bind = bind
    results = {r.chapter: r for r in batch.run_batch(novel, [1, 2, 3], steps, config=config, allowance=lambda c: 1)}
    assert results[2].outcome == batch.FAILED and "disk full" in results[2].reason
    assert results[1].outcome == results[3].outcome == batch.READY


def test_the_report_puts_what_needs_a_person_above_what_does_not(book):
    novel, config = book
    candidate(novel, config, 2, "a.xlsx", "b.xlsx")
    batch.run_batch(novel, [1, 2, 3], World(novel, config).steps(), config=config, allowance=lambda c: 1)
    text = (novel / "agent_storyboard_batch.md").read_text(encoding="utf-8")
    assert text.index("等人选稿") < text.index("可以渲染")
    assert "第 2 章：产出了 2 份分镜表" in text
    saved = json.loads((novel / "agent_storyboard_batch.json").read_text(encoding="utf-8"))
    assert (saved["done"], saved["total"]) == (3, 3)


@pytest.mark.parametrize("hour, expected", [(3, 4), (9, 4), (10, 2), (12, 2), (19, 2), (20, 4), (23, 4)])
def test_two_by_day_and_four_on_the_night_shift(hour, expected):
    config = {"parallel": {"day": 2, "night": 4, "night_from": 20, "night_until": 10}}
    assert parallel_now(config, time.struct_time((2026, 9, 22, hour, 0, 0, 1, 265, 0))) == expected


def test_a_config_that_says_nothing_about_it_runs_one_at_a_time():
    assert parallel_now({}) == 1
