"""A chapter's dialogue is 260 seconds of screen; an episode is 105.

Both planners kept 28% of a chapter's lines and the viewer could not tell what a scene was about.
The number of episodes a chapter needs follows from how much it says, and the cut between them has
to land where the story turns - not on the eight equal coverage segments, which straddle scenes as
often as not.  These tests are the arithmetic of that: how many, how long, what a sound cut is, and
the mechanical cut that stands in when nobody chose better.
"""
from __future__ import annotations

import json

import pytest

from novel_manga.planning import parts as cp

CH12 = [  # the shape of chapter 12: eight paragraphs of narration and speech, 1138 characters of dialogue
    "地狱厨房，窗户忽然一下打开，一辆银白色的机甲飞快的冲了进来。",
    "斯塔克站在桌子前打量了一圈屋内，略带嫌弃的说：“你这地方可真够破的。”",
    "席勒喝了口咖啡，慢条斯理的说：“我知道我的诊金很贵，但没关系，你可以再抱怨一会，只要我没进入正题，那就不收费。”“另外，你知道吗，你刚刚用手擦完我桌子上灰尘露出的那嫌弃的表情，比奶油蛋糕上面的樱桃还要娘。”“我承认这里环境的确不好，毕竟我不像你，是个亿万富翁，不过话说回来，当年霍华德先生应该也是在这样的一个破旧屋子里白手起家。”",
    "斯塔克直接从他冲进来的那扇窗户里又冲了出去。不过很快，斯塔克还是飞了回来，他没好气的说：“我给你打电话你不接，我只能飞到这个破烂堆来。你上次弄坏了我的贾维斯，今天我想给他升级时才发现他坏的很彻底，直接死机了，你得负责把他治好。”",
    "几分钟后，斯塔克和席勒出现在了地狱厨房破旧的公交站牌下面，斯塔克说：“我真不敢相信我跨时代战衣的首秀就是在一辆冒黑烟的破旧巴士上……”席勒摊摊手说：“因为钢铁侠扛着地狱巴士飞行的画面一定很美。”",
    "到了斯塔克的实验室之后，斯塔克站在一堆面板前，说：“我不知道你是怎么搞的，贾维斯死机了，他的硬件都是完好无损的，但他不愿意工作了。我想让贾维斯成为一个真正的电子生命，他可以成为一个全能的管家。”席勒说：“这其实是个悖论。机器生命在做选择时，遵循的永远是有利逻辑，但当有利逻辑和主人命令发生冲突时，感性和理性冲突之后，机器没法像人一样自欺欺人。”",
    "“如果你的父亲就要死了，而你的一个决定可以拯救他，他却极力反对，你要怎么做？”“如果你遵守他的意愿没有拯救他，他死了，你会后悔吗？”“如果他死了，你觉得他在临死前会后悔生了你吗？”斯塔克沉默了。“他不会那么做的。”斯塔克说。他的声音很低，但却很坚定，他说：“他不会阻止我救他，如果我因为救他而犯了大错，他会选择活过来以后，尽全力去弥补，哪怕再次付出生命。”",
    "斯塔克离开时的那种气势，让席勒觉得他恐怕真的能做到。不过他还是记得打电话给佩珀小姐，说：“佩珀小姐，午安，是这样的，我对斯塔克先生进行了一些亢奋疗法……呃，对，就是我自创的，但很有用。”席勒摸了摸下巴，要是能给他自己叠就好了。",
]


def test_paragraphs_are_numbered_the_way_the_coverage_segments_see_them():
    text = "第十二章 傲慢与偏见\n\n" + "\n\n".join(CH12)
    assert cp.paragraphs(text, "第十二章 傲慢与偏见") == CH12
    assert cp.paragraphs(text) == ["第十二章 傲慢与偏见", *CH12]      # the title stays unless named


def test_dialogue_is_what_is_inside_quotation_marks():
    assert cp.dialogue_chars(CH12[0]) == 0
    assert cp.dialogue_chars(CH12[1]) == len("你这地方可真够破的。")
    assert cp.dialogue_chars("他说：“好。”又说：「行了」") == 2 + 2
    assert cp.dialogue_chars("他说：「行」") == 0                       # one character is a stray pair, not a line


def test_a_chapter_wants_as_many_episodes_as_its_dialogue_fills():
    """1,138 characters at 3.1 a second is 367 seconds: three episodes, not one."""
    assert cp.suggest_count(["“" + "话" * 1138 + "”"]) == 3
    assert cp.suggest_count(CH12) == 2                              # the sample above is a condensed chapter
    assert cp.suggest_count(CH12[:2]) == 1
    assert cp.suggest_count(["“" + "话" * 700 + "”"]) == 3           # 226 s wants 3
    assert cp.suggest_count(["“" + "话" * 2000 + "”"]) == 3          # and never more than 3


def test_the_mechanical_cut_shares_the_dialogue_and_never_leaves_a_part_empty():
    parts = cp.mechanical_split(CH12, 3)
    assert [p.title for p in parts] == ["上", "中", "下"]
    assert parts[0].first == 1 and parts[-1].last == len(CH12)
    assert all(a.last + 1 == b.first for a, b in zip(parts, parts[1:]))
    shares = [p.dialogue_chars for p in parts]
    assert max(shares) < 2.2 * min(shares)                          # roughly equal, cut on paragraphs
    assert cp.validate(parts, CH12) == []                           # balanced by construction


def test_a_chapter_too_short_to_cut_stays_one_episode():
    assert [p.title for p in cp.mechanical_split(CH12[:2], 3)] == [""]
    assert cp.mechanical_split(CH12, 1)[0].reason == "整章一集"


def test_a_proposed_cut_is_checked_for_gaps_overlaps_and_order():
    rows = CH12
    ok = [cp.make_part(rows, 1, 3, 1, 3), cp.make_part(rows, 2, 3, 4, 6), cp.make_part(rows, 3, 3, 7, 8)]
    assert all("相差太多" in p for p in cp.validate(ok, rows))       # at most a balance note, no structural fault
    gap = [cp.make_part(rows, 1, 3, 1, 3), cp.make_part(rows, 2, 3, 5, 6), cp.make_part(rows, 3, 3, 7, 8)]
    assert any("首尾相接" in p for p in cp.validate(gap, rows))
    short = [cp.make_part(rows, 1, 2, 1, 3), cp.make_part(rows, 2, 2, 4, 7)]
    assert any("最后一集到第 7 段" in p for p in cp.validate(short, rows))
    assert cp.validate([], rows) == ["没有分出任何一集"]


def test_a_lopsided_cut_is_told_which_way_to_move_it():
    """Chapter 12 came back 129 / 326 / 202 seconds: the middle a whole conversation, the cut to redo."""
    rows = CH12
    lopsided = [cp.make_part(rows, 1, 2, 1, 1), cp.make_part(rows, 2, 2, 2, 8)]
    notes = cp.validate(lopsided, rows)
    assert any("第 2 集" in n and "往前挪" in n for n in notes)
    assert any("第 1 集" in n and "往后挪" in n for n in notes)
    assert not any("秒，目标" in n for n in notes)                  # no absolute target: a long chapter is long


def test_an_opening_phrase_finds_its_paragraph():
    assert cp.locate(CH12, "几分钟后，斯塔克和席勒") == 5
    assert cp.locate(CH12, "斯塔克和席勒出现在了") == 5             # contained, not leading
    assert cp.locate(CH12, "从未出现的话") is None
    assert cp.locate(CH12, "") is None


def test_parts_are_written_and_read_back_whole(tmp_path):
    parts = cp.mechanical_split(CH12, 3)
    cp.write_parts(tmp_path, parts, decided_by="test", chapter=12)
    saved = json.loads((tmp_path / "parts.json").read_text(encoding="utf-8"))
    assert saved["chapter"] == 12 and saved["count"] == 3 and saved["decided_by"] == "test"
    assert cp.read_parts(tmp_path) == parts
    assert cp.read_parts(tmp_path / "nowhere") == []


def test_part_text_is_the_paragraphs_and_nothing_else():
    part = cp.make_part(CH12, 2, 3, 4, 5)
    assert cp.part_text(CH12, part) == CH12[3] + "\n" + CH12[4]


@pytest.mark.parametrize("part, name", [(None, "meiman-daoshi_12"), (1, "meiman-daoshi_12-1"), (3, "meiman-daoshi_12-3")])
def test_a_part_has_its_own_episode_directory_beside_the_chapters(part, name):
    assert cp.part_dir_name("meiman-daoshi", 12, part) == name


def test_imbalance_is_longest_over_shortest():
    even = cp.mechanical_split(["“" + "话" * 50 + "”"] * 12, 2)
    assert 1.0 <= cp.imbalance(even) < 1.2
    lopsided = [cp.make_part(CH12, 1, 2, 1, 1), cp.make_part(CH12, 2, 2, 2, 8)]
    assert cp.imbalance(lopsided) > cp.IMBALANCE_LIMIT
    assert cp.imbalance([]) == 1.0


def test_a_cut_window_brackets_the_even_point_and_leaves_every_part_a_paragraph():
    rows = ["“" + "话" * 50 + "”"] * 10                    # ten equal paragraphs: thirds fall at 3.3 and 6.7
    (lo1, hi1), (lo2, hi2) = cp.cut_windows(rows, 3)
    assert lo1 <= 3 <= hi1 and lo2 <= 7 <= hi2
    assert lo1 >= 1 and hi2 <= 8                              # the last part keeps at least two paragraphs' room
    assert cp.cut_windows(rows, 1) == []


def test_a_cut_outside_its_window_is_named_by_paragraph():
    rows = ["“" + "话" * 50 + "”"] * 10
    windows = cp.cut_windows(rows, 2)
    early = [cp.make_part(rows, 1, 2, 1, 1), cp.make_part(rows, 2, 2, 2, 10)]
    notes = cp.validate(early, rows, windows)
    assert any("第 1 集必须在第" in n and "现在结束在第 1 段" in n for n in notes)
