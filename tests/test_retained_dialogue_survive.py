"""The outline's promised lines are a promise: the second pass must deliver each of them.

ch12 round five showed the gap: the coverage outline's retained_dialogue kept 席勒's bus punchline
(因为钢铁侠扛着地狱巴士飞行的画面一定很美), the second pass wrote the clinic and the lab and dropped
the bus scene whole - the joke the scene existed for was never spoken.  The check reads the quoted
lines from the outline section and holds the packed turns to them, with the same fuzzy match a
beat's own line gets: condensation is adaptation, loss is not.
"""
from novel_manga.planning.validation import retained_dialogue_issues, validate_and_normalize
from novel_manga.planning.issues import PlanningCode
from novel_manga.planning.context import PlannerContext
from novel_manga.models.bible import Character, StoryBible

OUTLINE = {"sections": {"retained_dialogue":
    "关键台词保留：斯塔克：“你把他弄坏的就得修好。”；席勒：“好吧，这次免费，我们过去吧。”；"
    "席勒：“因为钢铁侠扛着地狱巴士飞行的画面一定很美。”"}}


def speaks(name, text):
    return {"speaker_name": name, "delivery_mode": "visible_dialogue", "text": text}


def shot(*turns):
    return {"clip_hint": "clip_1", "label": "s1", "location": "诊所", "segment_id": "s1",
            "characters": [t["speaker_name"] for t in turns], "scene_id": "scene_01",
            "visual_prompt": "诊室内", "motion_prompt": "两人说话", "end_state": "话说完",
            "turns": list(turns), "actions": [], "extras": [],
            "camera": "", "light": "", "avoid": "", "sfx": "", "shot_scale": "中景", "origin_index": 1}


def test_every_promised_line_that_is_spoken_passes():
    issues = retained_dialogue_issues(OUTLINE["sections"]["retained_dialogue"],
                                      [shot(speaks("托尼·斯塔克", "你把他弄坏的就得修好。"),
                                            speaks("席勒", "好吧，这次免费，我们过去吧。"),
                                            speaks("席勒", "因为钢铁侠扛着地狱巴士飞行的画面一定很美。"))])
    assert issues == []


def test_the_bus_punchline_promised_and_dropped_is_reported():
    """The round-five defect: the joke the bus scene existed for, kept by the outline, lost by
    the second pass."""
    issues = retained_dialogue_issues(OUTLINE["sections"]["retained_dialogue"],
                                      [shot(speaks("托尼·斯塔克", "你把他弄坏的就得修好。"),
                                            speaks("席勒", "好吧，这次免费，我们过去吧。"))])
    assert len(issues) == 1 and "钢铁侠扛着地狱巴士飞行" in issues[0]


def test_a_condensed_paraphrase_still_counts():
    """Condensation is adaptation, not loss: a shortened punchline carries the promise."""
    issues = retained_dialogue_issues(OUTLINE["sections"]["retained_dialogue"],
                                      [shot(speaks("托尼·斯塔克", "你把他弄坏的就得修好。"),
                                            speaks("席勒", "这次免费，我们过去吧。"),
                                            speaks("席勒", "钢铁侠扛着巴士飞，画面一定很美。"))])
    assert issues == []


def test_short_quotes_are_not_promises():
    """A term like “叠buff” is not a line; the section mentions such words without promising them."""
    issues = retained_dialogue_issues("保留“叠buff”“亢奋疗法”等词。", [shot(speaks("席勒", "你好。"))])
    assert issues == []


def test_no_section_or_empty_section_is_no_check():
    assert retained_dialogue_issues("", [shot(speaks("席勒", "你好。"))]) == []
    assert retained_dialogue_issues(None, []) == []


def test_validate_plan_holds_the_second_pass_to_the_outline():
    """Through the full validator: the outline rides on the context, and the lost line is an error."""
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
    assert PlanningCode.RETAINED_LINE_LOST in codes
    message = next(issue.message for issue in result.issues if issue.code == PlanningCode.RETAINED_LINE_LOST)
    assert "钢铁侠扛着地狱巴士飞行" in message and "第二遍必须逐条落实" in message
