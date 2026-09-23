"""Presence is graded, not guessed: a name in the picture text is on camera, a name in a spoken
line is only talked about.

The review's case: 席勒提到佩珀担心托尼 must not seat a 佩珀 card, while 托尼背对镜头站在席勒
旁边 must keep his.  complete_characters used one rule for both - whatever the entity index
could name, the scan added - so the moment a book builds its index (在美漫当心灵导师的日子 has
not yet; the test suite pretends it has), every spoken name would walk into the cast.  The
grades live in presence_candidates; the cast only takes on_camera ones.
"""
from novel_manga.planning import cast as pc_cast
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.normalization import cast_and_actions

NAMES = ["席勒", "托尼·斯塔克", "佩珀", "贾维斯"]
EVERYONE = tuple(NAMES)


def indexed_ctx():
    ctx = PlannerContext()
    for name in NAMES:
        ctx.entity_forms[name] = {name}
        ctx.entity_generic[name] = False
    return ctx


def shot(**kw):
    base = {"characters": ["席勒", "托尼·斯塔克"], "visual_prompt": "", "motion_prompt": "",
            "end_state": "", "turns": []}
    base.update(kw)
    return base


def test_a_spoken_name_is_a_candidate_not_a_body():
    ctx = indexed_ctx()
    s = shot(turns=[{"speaker_name": "托尼·斯塔克", "text": "我让佩珀炒了你。", "delivery_mode": "visible_dialogue"}])
    cast, added = pc_cast.complete_characters(list(s["characters"]), s, list(EVERYONE), ctx=ctx)
    assert added == [] and "佩珀" not in cast
    candidates = pc_cast.presence_candidates(list(s["characters"]), s, list(EVERYONE), ctx=ctx)
    assert candidates["佩珀"] == [{"field": "turns", "grade": "talked_about"}]


def test_a_name_the_picture_paints_still_joins_the_cast():
    """The 761 line holds: the description said 薇奥拉 kissed 莱恩, and she must be cast."""
    ctx = indexed_ctx()
    s = shot(visual_prompt="佩珀站在门口，托尼·斯塔克背对镜头站在席勒旁边")
    cast, added = pc_cast.complete_characters(list(s["characters"]), s, list(EVERYONE), ctx=ctx)
    assert added == ["佩珀"]


def test_a_name_painted_and_spoken_is_on_camera():
    ctx = indexed_ctx()
    s = shot(visual_prompt="佩珀站在门口",
             turns=[{"speaker_name": "托尼·斯塔克", "text": "佩珀，你来的正好。", "delivery_mode": "visible_dialogue"}])
    candidates = pc_cast.presence_candidates(list(s["characters"]), s, list(EVERYONE), ctx=ctx)
    assert any(e["grade"] == "on_camera" for e in candidates["佩珀"])
    assert pc_cast.complete_characters(list(s["characters"]), s, list(EVERYONE), ctx=ctx)[1] == ["佩珀"]


def test_the_cast_itself_never_counts_as_a_candidate():
    ctx = indexed_ctx()
    s = shot(visual_prompt="席勒坐在桌后，托尼·斯塔克坐在对面")
    assert pc_cast.presence_candidates(list(s["characters"]), s, list(EVERYONE), ctx=ctx) == {}


def test_normalization_reports_the_talked_about_without_adding_them():
    ctx = indexed_ctx()
    errors, warnings = [], []
    s = shot(characters=["席勒"], turns=[{"speaker_name": "席勒", "text": "可怜的贾维斯，被你弄坏了。", "delivery_mode": "visible_dialogue"}],
             actions=[{"actor": "席勒", "action": "说话", "target": ""}])
    characters, _, _, _, _ = cast_and_actions(
        {"location": "诊所", **s}, NAMES, EVERYONE, {"诊所": "诊所"}, "stage 1", ctx, errors, warnings)
    assert "贾维斯" not in characters
    assert any("台词提及" in w and "贾维斯" in w for w in warnings)


def test_tony_keeps_his_reference_when_he_stands_in_the_picture():
    """The other half of the review's line: 真实在画面的托尼背影 must not lose his card to the
    grading - the picture painting him is on_camera evidence, whatever else the lines say."""
    ctx = indexed_ctx()
    s = shot(characters=["席勒"], visual_prompt="托尼·斯塔克背对镜头站在席勒旁边",
             turns=[{"speaker_name": "席勒", "text": "斯塔克先生，喝咖啡吗。", "delivery_mode": "visible_dialogue"}],
             actions=[{"actor": "席勒", "action": "递", "target": "托尼·斯塔克"}])
    characters, _, _, _, _ = cast_and_actions(
        {"location": "诊所", **s}, NAMES, EVERYONE, {"诊所": "诊所"}, "stage 1", ctx, [], [])
    assert "托尼·斯塔克" in characters
