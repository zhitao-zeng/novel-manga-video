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
    (novel / "story_bible.json").write_text(json.dumps({
        "novel_title": "测试", "genre": "通用", "visual_style": "美漫", "palette": "冷蓝",
        "style_fingerprint": "test", "locations": ["哥谭大学阶梯教室：下坡式地面上成排的深色长条木桌"],
        "characters": [{"name": "席勒", "role": "人物", "appearance": "黑发", "wardrobe": "外套"}]},
        ensure_ascii=False), encoding="utf-8")
    return novel, {"runs_root": str(tmp_path / "runs"), "parallel": {"day": 2, "night": 4}}


def locations(novel):
    return json.loads((novel / "story_bible.json").read_text(encoding="utf-8"))["locations"]


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


# ---- the places a take invents ---------------------------------------------------------------------

# The two proposals on record, as the agent wrote them.  The brief fixes no layout and they differ.
CH11 = """# 新增地点

本集（第 11 章）实际出现了《人物地点与画风》名单中没有的两个地点，按规则自行命名并登记空场描写。

## 1. 莫森街区小巷的二层阳台

莫森街区小巷内侧一栋老式公寓楼的第二层：突出的木质阳台，木质栏杆漆面剥落，檐口垂着一线雨水，阳台门是一扇旧玻璃木门。时段为深夜，主光源为巷口路灯的昏黄光。

## 2. 贫民住宅楼外

哥谭贫民区一栋四至五层破旧住宅楼的外立面夜间空景：斑驳脱落的灰黑外墙，成排小窗与外挂的空调外机。主光源为住户窗光与远处一盏路灯。

## 使用说明（分镜表「场景」列对应）

- 镜 1、2、3 → 莫森街区小巷的二层阳台
- 镜 8 → 贫民住宅楼外
"""

CH10 = """# 新增地点

本章原文有三场课堂戏，名单里没有**教室**。按任务规则新增一个地点：

## 哥谭大学教室

**「场景」列写法（必须一字不差）：** 哥谭大学教室

**空场描写（长期不变的样子，无人物）：**

哥谭大学主楼内的一间阶梯大教室。整面墙的高大拱形木框窗常年蒙着雨痕，透进冷灰的日光；下坡式地面上成排的深色长条木桌与连体座椅。主光源为拱窗天光。

**本集镜位：** 镜 1（白天）。
"""


def test_the_description_is_found_under_either_of_the_layouts_the_agent_has_used():
    assert agent_storyboard.proposed_description(CH11, "贫民住宅楼外").startswith("哥谭贫民区一栋四至五层破旧住宅楼")
    assert agent_storyboard.proposed_description(CH11, "莫森街区小巷的二层阳台").endswith("昏黄光。")
    found = agent_storyboard.proposed_description(CH10, "哥谭大学教室")
    assert found.startswith("哥谭大学主楼内的一间阶梯大教室") and "一字不差" not in found and "镜位" not in found


def test_a_section_that_is_not_a_place_is_not_mistaken_for_one():
    """使用说明 sits under the same kind of heading as the places do."""
    assert agent_storyboard.proposed_description(CH11, "使用说明") == ""
    assert agent_storyboard.proposed_description(CH11, "凉亭") == ""


def accepted_with(novel, config, chapter, place, proposal=None):
    shots = [[*SHOTS[0][:4], place, *SHOTS[0][5:]], SHOTS[1]]
    episode = candidate(novel, config, chapter, "分镜表.xlsx", worksheets={f"第{chapter}集": shots})
    if proposal is not None:
        (tmp_runs(config) / agent_storyboard.run_name(novel.name, chapter, "shanyin") / "output" / "新增地点.md"
         ).write_text(proposal, encoding="utf-8")
    agent_storyboard.auto_accept(episode, config=config)
    return episode


def test_a_place_the_take_invented_goes_into_the_bible_after_everything_already_there(book):
    """Ids are positions: a place may only ever be added at the end."""
    novel, config = book
    before = locations(novel)
    episode = accepted_with(novel, config, 11, "贫民住宅楼外", CH11)
    added, problem = agent_storyboard.place_new_locations(novel, episode, config=config)
    assert (added, problem) == (["贫民住宅楼外"], "")
    after = locations(novel)
    assert after[:len(before)] == before
    assert after[-1].startswith("贫民住宅楼外：哥谭贫民区一栋四至五层破旧住宅楼")


def test_the_proposal_travels_with_the_take_it_belongs_to(book):
    """output/ is rewritten by the next attempt; a sheet whose places are described elsewhere is half a take."""
    novel, config = book
    episode = accepted_with(novel, config, 11, "贫民住宅楼外", CH11)
    assert (episode / "agent_storyboard" / "新增地点.md").read_text(encoding="utf-8") == CH11


def test_a_chapter_that_stays_in_known_places_changes_nothing(book):
    novel, config = book
    before = locations(novel)
    episode = accepted_with(novel, config, 3, "哥谭大学阶梯教室")
    assert agent_storyboard.place_new_locations(novel, episode, config=config) == ([], "")
    assert locations(novel) == before


def test_a_new_place_with_no_proposal_stops_for_a_person_rather_than_going_in_bare(book):
    """A name with no description is a card drawn from nothing, and paid for."""
    novel, config = book
    before = locations(novel)
    episode = accepted_with(novel, config, 11, "贫民住宅楼外")
    added, problem = agent_storyboard.place_new_locations(novel, episode, config=config)
    assert added == [] and "贫民住宅楼外" in problem and "没写 新增地点.md" in problem
    assert locations(novel) == before


def test_a_new_place_the_proposal_does_not_describe_stops_too(book):
    novel, config = book
    episode = accepted_with(novel, config, 11, "废弃码头仓库", CH11)
    added, problem = agent_storyboard.place_new_locations(novel, episode, config=config)
    assert added == [] and "废弃码头仓库" in problem and "找不到它的空场描写" in problem


def test_two_chapters_inventing_the_same_alley_add_it_once(book):
    novel, config = book
    first = accepted_with(novel, config, 11, "贫民住宅楼外", CH11)
    second = accepted_with(novel, config, 12, "贫民住宅楼外", CH11)
    assert agent_storyboard.place_new_locations(novel, first, config=config)[0] == ["贫民住宅楼外"]
    assert agent_storyboard.place_new_locations(novel, second, config=config) == ([], "")
    assert sum(entry.startswith("贫民住宅楼外：") for entry in locations(novel)) == 1


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


def test_a_place_that_cannot_be_put_in_stops_the_chapter_before_binding(book):
    novel, config = book
    world = World(novel, config)
    inner = world.propose
    def propose(chapter):
        inner(chapter)
        accepted_with(novel, config, chapter, "废弃码头仓库", CH11)
    steps = world.steps()
    steps.propose = propose
    result = batch.run_chapter(novel, 7, steps, config=config)
    assert result.outcome == batch.LOOK and "废弃码头仓库" in result.reason
    assert ("bind", 7) not in world.calls


def test_a_place_that_went_in_is_said_out_loud_and_the_chapter_goes_on(book):
    novel, config = book
    world = World(novel, config)
    inner = world.propose
    def propose(chapter):
        inner(chapter)
        accepted_with(novel, config, chapter, "贫民住宅楼外", CH11)
    steps = world.steps()
    steps.propose = propose
    result = batch.run_chapter(novel, 7, steps, config=config)
    assert result.outcome == batch.READY and ("bind", 7) in world.calls
    assert any("新地点已补进圣经：贫民住宅楼外" in line for line in world.said)


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
