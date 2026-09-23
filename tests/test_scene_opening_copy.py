"""The second pass must open the new scene on the new scene, not on a copy of the old one.

The ch12 replan (same source, same bible, same model, new prompts) produced the bus-stop shot
opening with the clinic's whole event sentence - the outline's scene_handoffs said what the scene
opens on, and the copy contradicted it before any rendering happened.  Round three copied the
scene's OPENING shot rather than its closing one, so the checker holds every shot of the previous
scene.  The hand-off pair rides along here too: its question must be heard, whatever the outline
did with it.
"""
from novel_manga.planning.validation import (_copied_from_previous_scene, handoff_lines_survive,
                                              validate_and_normalize)
from novel_manga.planning.issues import PlanningCode
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.text import quote_key
from novel_manga.models.bible import Character, StoryBible


CLINIC_EVENT = "满地纸页间，席勒站直身体，长出一口气、舒展肩膀。窗户再次被撞开，马克2号落回桌前"


def clinic_then_bus_stop(copy: bool):
    """Two shots, two scenes; the second's opening is (or is not) the clinic's event text."""
    return [
        {"clip_hint": "clip_1", "label": "s1", "location": "地狱厨房第九尾巷心理诊所", "segment_id": "s1",
         "characters": ["席勒"], "scene_id": "scene_01",
         "visual_prompt": "诊室里纸页散落，席勒站在桌后", "motion_prompt": CLINIC_EVENT,
         "end_state": "斯塔克落回桌前开口", "turns": [], "actions": [], "extras": [],
         "camera": "", "light": "", "avoid": "", "sfx": "", "shot_scale": "中景", "origin_index": 1},
        {"clip_hint": "clip_2", "label": "s2", "location": "地狱厨房公交站牌", "segment_id": "s2",
         "characters": ["席勒"], "scene_id": "scene_02",
         "visual_prompt": (CLINIC_EVENT + "，然后两人在站牌下") if copy else "锈迹斑斑的金属站牌下，老旧巴士驶来停靠",
         "motion_prompt": "巴士停下，车门打开", "end_state": "两人准备上车",
         "turns": [{"speaker_name": "席勒", "delivery_mode": "visible_dialogue", "text": "钢铁侠扛着巴士飞，画面一定很美。"}],
         "actions": [], "extras": [], "camera": "", "light": "", "avoid": "", "sfx": "",
         "shot_scale": "全景", "origin_index": 2},
    ]


def test_a_copied_opening_is_flagged():
    first, second = clinic_then_bus_stop(copy=True)
    assert _copied_from_previous_scene(quote_key(second["visual_prompt"]), first) is not None


def test_the_copy_may_come_from_any_shot_of_the_previous_scene():
    """Round three: the new scene copied the previous scene's OPENING shot, not its closing one."""
    opening_shot = {"visual_prompt": "诊室里纸页散落，席勒站在桌后，手端一杯冒热气的咖啡，昏黄台灯亮着",
                    "motion_prompt": "开场瞬间：诊室的静物一览", "end_state": "静物定格"}
    second = {"visual_prompt": "诊室里纸页散落，席勒站在桌后，手端一杯冒热气的咖啡，昏黄台灯亮着，然后两人在站牌下",
              "motion_prompt": "", "end_state": ""}
    assert _copied_from_previous_scene(quote_key(second["visual_prompt"]), opening_shot) is not None


def test_an_original_opening_is_not():
    first, second = clinic_then_bus_stop(copy=False)
    assert _copied_from_previous_scene(quote_key(second["visual_prompt"]), first) is None


def test_handoff_question_must_be_heard():
    """The pair ch12 lost both routes: the question unspoken is an error naming the next scene."""
    shots = [{"turns": [{"speaker_name": "席勒", "delivery_mode": "visible_dialogue",
                         "text": "这次免费。"}], "visual_prompt": "", "motion_prompt": ""}]
    errors = []
    handoff_lines_survive(shots, [("你该不会想让我坐这个过去吧？", "不然呢？你打算怎么过去？")], errors)
    assert len(errors) == 1 and errors[0].code == PlanningCode.HANDOFF_LINE_LOST
    assert "下一场" in errors[0].message


def test_a_heard_question_passes_and_the_answer_may_be_a_picture():
    shots = [{"turns": [{"speaker_name": "席勒", "delivery_mode": "visible_dialogue",
                         "text": "你该不会想让我坐这个过去吧？"}],
              "visual_prompt": "", "motion_prompt": ""}]
    errors = []
    handoff_lines_survive(shots, [("你该不会想让我坐这个过去吧？", "不然呢？你打算怎么过去？")], errors)
    assert errors == []                      # the bus stop itself answers; spoken is one option


def _bible():
    return StoryBible(novel_title="t", genre="g", visual_style="3d 国漫", palette="冷", style_fingerprint="fp",
                      characters=[Character(name="席勒", role="男主角", appearance="清瘦", wardrobe="白大褂")],
                      locations=["地狱厨房第九尾巷心理诊所：低矮房间", "地狱厨房公交站牌：站牌"])


def test_validate_plan_reports_the_copied_opening():
    """Through the full validator: the copy is an error naming the hand-off, the original passes."""
    import novel_manga.planning.validation as validation
    raw = {"video_title": "t", "hook": "h", "summary": "s", "clips": [
        {"clip_id": "clip_1", "location": "地狱厨房第九尾巷心理诊所", "characters": ["席勒"], "avoid": "",
         "stages": [{"segment_id": "s1", "source_quote": "十六个字以上的原文引用，用来通过引用检查的句子。", "start_state": "诊室里纸页散落",
                     "event": CLINIC_EVENT,
                     "end_state": "斯塔克落回桌前开口", "camera": "", "light": "", "sfx": "",
                     "shot_scale": "中景", "turns": [], "in_frame": ["席勒"], "actions": [], "extras": [],
                     "scene_id": "scene_01"}]},
        {"clip_id": "clip_2", "location": "地狱厨房公交站牌", "characters": ["席勒"], "avoid": "",
         "stages": [{"segment_id": "s2", "source_quote": "另一段十六个字以上的原文引用，用来通过检查的句子。",
                     "start_state": CLINIC_EVENT + "，然后两人在站牌下",
                     "event": "巴士停下，车门打开", "end_state": "两人准备上车", "camera": "", "light": "", "sfx": "",
                     "shot_scale": "全景",
                     "turns": [{"speaker_name": "席勒", "delivery_mode": "visible_dialogue", "text": "钢铁侠扛着巴士飞，画面一定很美。"}],
                     "in_frame": ["席勒"], "actions": [], "extras": [], "scene_id": "scene_02"}]}],
        "skipped_segments": []}
    ctx = PlannerContext()
    ctx.authored_storyboard = True          # a cut is the author's: framing must not re-split
    bible = _bible()
    location_map = {str(entry).split("：", 1)[0]: entry for entry in bible.locations}
    result = validation.validate_and_normalize(raw, [{"segment_id": "s1", "text": "满地纸页间，席勒站直身体，长出一口气。"},
                                                     {"segment_id": "s2", "text": "巴士在站牌下停靠，车门打开。"}],
                                               bible, location_map,
                                               "满地纸页间，席勒站直身体，长出一口气。巴士在站牌下停靠，车门打开。",
                                               ctx=ctx, everyone=["席勒"])
    codes = [issue.code for issue in result.issues]
    assert PlanningCode.SCENE_OPENING_COPIED in codes
    message = next(issue.message for issue in result.issues if issue.code == PlanningCode.SCENE_OPENING_COPIED)
    assert "scene_handoffs" in message and "上一场" in message


def test_validate_plan_demands_the_handoff_question():
    """Through the full validator with the pair on the context: the lost question is an error."""
    import novel_manga.planning.validation as validation
    raw = {"video_title": "t", "hook": "h", "summary": "s", "clips": [
        {"clip_id": "clip_1", "location": "地狱厨房第九尾巷心理诊所", "characters": ["席勒"], "avoid": "",
         "stages": [{"segment_id": "s1", "source_quote": "十六个字以上的原文引用，用来通过引用检查的句子。",
                     "start_state": "诊室里纸页散落", "event": "席勒答应免费", "end_state": "两人动身",
                     "camera": "", "light": "", "sfx": "", "shot_scale": "中景",
                     "turns": [{"speaker_name": "席勒", "delivery_mode": "visible_dialogue", "text": "这次免费。"}],
                     "in_frame": ["席勒"], "actions": [], "extras": []}]},
        {"clip_id": "clip_2", "location": "地狱厨房公交站牌", "characters": ["席勒"], "avoid": "",
         "stages": [{"segment_id": "s2", "source_quote": "另一段十六个字以上的原文引用，用来通过检查的句子。",
                     "start_state": "站牌下巴士驶来", "event": "两人上车", "end_state": "巴士驶离",
                     "camera": "", "light": "", "sfx": "", "shot_scale": "全景",
                     "turns": [{"speaker_name": "席勒", "delivery_mode": "visible_dialogue", "text": "钢铁侠扛着巴士飞，画面一定很美。"}],
                     "in_frame": ["席勒"], "actions": [], "extras": []}]},
        ],
        "skipped_segments": []}
    ctx = PlannerContext()
    ctx.handoff_pairs = [("你该不会想让我坐这个过去吧？", "不然呢？你打算怎么过去？")]
    bible = _bible()
    location_map = {str(entry).split("：", 1)[0]: entry for entry in bible.locations}
    result = validation.validate_and_normalize(raw, [{"segment_id": "s1", "text": "席勒答应免费，两人动身。"},
                                                     {"segment_id": "s2", "text": "站牌下巴士驶来，两人上车。"}],
                                               bible, location_map,
                                               "席勒答应免费，两人动身。站牌下巴士驶来，两人上车。",
                                               ctx=ctx, everyone=["席勒"])
    codes = [issue.code for issue in result.issues]
    assert PlanningCode.HANDOFF_LINE_LOST in codes
