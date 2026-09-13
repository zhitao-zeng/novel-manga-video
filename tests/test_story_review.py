"""The clip judge reads the book: it is handed the clip's source passage, and a picture that tells the wrong
story - someone the text puts in motion is absent, or their action is done by someone else - is a retake,
with the judge's own sentence as the correction."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import thin_review  # noqa: E402
from novel_manga.models import Character, StoryBible  # noqa: E402

PROMPT = "【生成目标】约12秒。主要事件是从“薇奥拉环住莱恩的脖子踮脚吻他”到“莱恩推开怀里的姑娘”。\n【人物】"


def bible() -> StoryBible:
    return StoryBible(novel_title="雾月", genre="gaslamp", visual_style="3d", palette="p", style_fingerprint="f",
                      characters=[Character(name="莱恩·格雷", role="主角", appearance="青年", wardrobe="大衣"),
                                  Character(name="琥珀·高德", role="独立角色", appearance="橘猫", wardrobe="无")])


def verdict(**extra) -> dict:
    return {"severity": "fail", "identity_ok": True, "identity_issue": "", "defect_issue": "", "visual_defects": False,
            "story_ok": False, "story_kind": "动作落在错误的人物身上",
            "story_issue": "原文是薇奥拉环住莱恩的脖子吻他，画面里是橘猫琥珀搂着莱恩的脖子亲吻，薇奥拉不在画面里", **extra}


def test_schema_asks_about_the_story():
    props = thin_review.CLIP_SCHEMA["properties"]
    assert {"story_ok", "story_kind", "story_issue"} <= set(props)
    assert set(props["story_kind"]["enum"]) == set(thin_review.STORY_KINDS)
    assert thin_review.STORY_FATAL < set(thin_review.STORY_KINDS)


def test_story_block_carries_the_passage_and_the_event():
    block = thin_review.story_block({"prompt": PROMPT, "segment_ids": ["seg_4", "seg_5"]},
                                    {"seg_4": "她吻住了莱恩。", "seg_5": "“喵~”月下的小琥珀叫了一声。"})
    assert "她吻住了莱恩" in block and "小琥珀" in block and "薇奥拉环住莱恩的脖子" in block
    assert thin_review.story_block({"prompt": "", "segment_ids": []}, {}) == "\n本段原文（剧情依据）：（无）\n"


def test_wrong_actor_or_missing_actor_is_a_retake():
    assert thin_review.fix_tier(verdict(), bible()) == "must_fix"
    assert thin_review.fix_tier(verdict(story_kind="原文中有动作的人物缺席"), bible()) == "must_fix"
    assert thin_review.fix_tier(verdict(story_kind="画面事件与原文不符"), bible()) == "ignore"
    assert thin_review.fix_tier(verdict(story_ok=True, story_kind="无问题", story_issue=""), bible()) == "ignore"
    # the book asked for it: the script check still wins
    assert thin_review.fix_tier(verdict(scripted={"evidence": "x", "note": ""}), bible()) == "optional"


def test_the_correction_repeats_the_judge_in_the_books_terms():
    note = thin_review.compose_feedback(verdict())
    assert note.startswith("按原文修正剧情：原文是薇奥拉环住莱恩的脖子吻他")
    assert thin_review.flag_line("clip_05", verdict(), "must_fix").startswith("clip_05: 原文是薇奥拉")


def test_old_verdicts_without_story_fields_are_unchanged():
    old = {"severity": "fail", "identity_ok": False, "identity_issue": "莱恩·格雷被画成了另一个人，完全一致", "defect_issue": "", "visual_defects": False}
    assert thin_review.fix_tier(old, bible()) == "must_fix"
    assert "按原文修正剧情" not in thin_review.compose_feedback(old)
