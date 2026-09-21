"""A colon after a name is not a chat bubble.

Two chapters of 在美漫当心灵导师的日子 were refused for having no chat_message in a conversation nobody
had online.  The detector knew that 席勒说：“…” is prose, and only that exact shape: 席勒在脑子里问：“…”,
彼得叹了口气说：“…” and 他说：“…” all counted, and so did 《海贼：开局签到亚人血统》 - a book in the
author's end-of-chapter promo, whose colon belongs to its title.
"""
from __future__ import annotations

import pytest

from novel_manga.planning.context import PlannerContext
from novel_manga.planning.source_checks import chapter_coverage


def refused_for_chat(text: str, *, speakers=("席勒", "彼得")) -> bool:
    ctx = PlannerContext.from_env()
    ctx.max_skipped = 0
    errors, warnings = [], []
    chapter_coverage({"skipped_segments": []}, [{"turns": []}], [], set(), text, ctx,
                     errors, warnings, known_speakers=speakers)
    return any("聊天消息" in issue.message for issue in errors)


PROSE = "\n".join([
    '斯塔克说：“听着，制动系统已经完全损坏了。”',
    '席勒在脑子里问：“最好的应用形式是什么？”',
    '席勒说：“好好好，是你厉害。”',
    '彼得叹了口气说：“我以为我计划得已经很周密了。”',
    '席勒摇摇头说：“这个靶眼不是什么反侦察大师。”',
    '他说：“那大概是因为，我真是太会交朋友了。”',
])

PROMO = "\n".join(["另外还要推的书有：", "《海贼：开局签到亚人血统》", "《海贼：工资到位，四皇踢废》",
                   "《火影：开局忍界大战》", "《斗罗：我的武魂是黑洞》", "《遮天：我为帝子》"])

CHAT = "\n".join(["小明：今晚吃什么", "阿强：随便", "小明：那就火锅",
                  "阿强：好啊", "小明：六点老地方", "阿强：收到"])


def test_a_speech_attribution_is_prose_whatever_leads_into_it():
    assert not refused_for_chat(PROSE)


def test_an_attribution_for_somebody_not_on_the_speaker_list_is_still_prose():
    """他说 and 斯塔克说 are attributions too; the old rule only knew the names it was handed."""
    assert not refused_for_chat(PROSE, speakers=())


def test_a_book_title_is_not_a_nickname():
    assert not refused_for_chat(PROMO)


def test_a_real_chat_is_still_caught():
    """Six lines, two people, no speech verbs, no quotes: this is a screen, and it must be drawn as one."""
    assert refused_for_chat(CHAT)


def test_a_chat_buried_in_prose_is_still_caught():
    assert refused_for_chat(PROSE + "\n" + CHAT)


def test_one_label_used_once_each_is_a_stat_block_not_a_chat():
    block = "\n".join(["法宝名称：青莲剑", "法宝属性：火", "法宝等级：地阶",
                       "法宝来历：上古", "法宝价值：连城", "法宝备注：无"])
    assert not refused_for_chat(block)
