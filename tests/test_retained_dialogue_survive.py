"""Structural validation leaves adaptation meaning to the existing plot review."""
from novel_manga.planning.validation import validate_and_normalize
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.issues import PlanningCode
from novel_manga.models.bible import Character, StoryBible
OUTLINE = {"sections": {"retained_dialogue": "保留故事主线，允许改写"}}

def test_structural_validator_leaves_meaning_to_the_planning_review():
    """String similarity cannot settle whether a rewritten line kept its meaning."""
    raw = {"video_title": "t", "hook": "h", "summary": "s", "clips": [
        {"clip_id": "clip_1", "location": "诊所", "characters": ["托尼·斯塔克", "席勒"], "avoid": "",
         "stages": [{"segment_id": "s1", "source_quote": "十六个字以上的原文引用，用来通过引用检查的句子。",
                     "start_state": "诊室内", "event": "两人对话", "end_state": "话说完",
                     "camera": "", "light": "", "sfx": "", "shot_scale": "中景",
                     "turns": [{"speaker_name": "托尼·斯塔克", "delivery_mode": "visible_dialogue", "text": "你把他弄坏的就得修好。"},
                               {"speaker_name": "席勒", "delivery_mode": "visible_dialogue", "text": "好吧，这次免费，我们过去吧。"}],
                     "in_frame": ["托尼·斯塔克", "席勒"], "actions": [], "extras": []}]},
        ],
        "skipped_segments": []}
    ctx = PlannerContext()
    ctx.outline = OUTLINE
    ctx.authored_storyboard = True          # the cut is an author's: framing must not re-split
    bible = StoryBible(novel_title="t", genre="g", visual_style="3d 国漫", palette="冷", style_fingerprint="fp",
                       characters=[Character(name="托尼·斯塔克", role="主角", appearance="瘦高", wardrobe="便装"),
                                   Character(name="席勒", role="男主角", appearance="清瘦", wardrobe="白大褂")],
                       locations=["诊所：低矮房间"])
    location_map = {"诊所": "诊所：低矮房间"}
    result = validate_and_normalize(raw, [{"segment_id": "s1", "text": "托尼找席勒修贾维斯，席勒答应免费同去。"}],
                                    bible, location_map,
                                    "托尼找席勒修贾维斯，席勒答应免费同去。因为钢铁侠扛着地狱巴士飞行的画面一定很美。",
                                    ctx=ctx, everyone=["托尼·斯塔克", "席勒"])
    codes = [issue.code for issue in result.issues]
    assert PlanningCode.RETAINED_LINE_LOST not in codes
