"""fix_tier says must_fix for what a viewer notices - not for the bible's own words quoted back, not for
extras the setting did not list, not for a missing prop."""
from __future__ import annotations

import sys
from pathlib import Path

import novel_manga.review.policy as review_policy  # noqa: E402
from novel_manga.models.bible import Character, StoryBible


def bible() -> StoryBible:
    return StoryBible(
        novel_title="雾月", genre="gaslamp", visual_style="3d", palette="p", style_fingerprint="f",
        characters=[Character(name="莱恩·格雷", role="主角", appearance="青年", wardrobe="黑色大衣"),
                    Character(name="琥珀·高德", role="独立角色", appearance="非人类形态，橘色家猫", wardrobe="无"),
                    Character(name="莎拉", appearance="老妇", wardrobe="长裙")],
    )


def verdict(issue: str, **extra) -> dict:
    return {"severity": "fail", "identity_ok": False, "identity_issue": issue, "defect_issue": "", "visual_defects": False, **extra}


def test_bible_phrase_is_not_a_breakdown():
    issue = "琥珀·高德的角色卡设定为女性，非人类形态（橘色家猫），但画面中是穿西装的男性"
    assert not review_policy.BREAKDOWN.search(issue)
    assert review_policy.BREAKDOWN.search("手指非人，六指且穿模")


def test_extras_the_setting_did_not_list_are_not_missing():
    issue = "图5中出现了两名兜帽男性，这些角色在设定中未出现，属于多出的人物"
    assert not review_policy.MISSING.search(issue)
    assert review_policy.fix_tier(verdict(issue), bible()) == "optional"


def test_a_missing_prop_is_not_a_missing_character():
    issue = "莎拉的眼镜特征缺失，道具缺失"
    assert not review_policy.MISSING.search(issue)
    assert review_policy.fix_tier(verdict(issue), bible()) == "optional"


def test_an_absent_named_character_is_must_fix():
    issue = "设定中应出场的莱恩·格雷在所有帧中均未出现，画面中仅有莎拉一人"
    assert review_policy.MISSING.search(issue)
    assert review_policy.fix_tier(verdict(issue), bible()) == "must_fix"
    assert review_policy.fix_tier(verdict("莎拉完全缺失，未出场"), bible()) == "must_fix"
