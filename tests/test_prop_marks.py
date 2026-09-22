"""The judge's post-plan prop marking: one call per chapter, unioned with any planner marks.

The pilot showed the planner's model (flashnext) answering the optional props field empty on
every stage while the judge (27B) marked the acquisition shot correctly.  So marking rides the
judge channel, after validation, before the chapter script is written.
"""
from novel_manga.application.planning import prop_marks
from novel_manga.models.bible import Prop


PROPS = [Prop(name="功法玉简", category="法器", appearance="金色玉石", first_chapter=1411, quote="…"),
         Prop(name="冰霜之刃", category="武器", appearance="", first_chapter=1411, quote="…")]

SHOTS = [
    {"origin_index": 1, "motion_prompt": "沈玄川伸手凌空一抓，幽光化作玉石落入掌心", "visual_prompt": ""},
    {"origin_index": 2, "motion_prompt": "他走回宿舍躺下", "visual_prompt": ""},
]


def test_judge_marks_and_planner_marks_unite(monkeypatch):
    monkeypatch.setattr(prop_marks, "ask_json",
                        lambda parts, schema, **kw: {"marks": [["功法玉简"], []]})
    shots = [dict(s) for s in SHOTS]
    shots[1]["props"] = ["冰霜之刃"]                    # 规划器自己标了一个
    marked = prop_marks.mark_props(shots, PROPS)
    assert marked == {1: ["功法玉简"], 2: ["冰霜之刃"]}  # 判官标的 ∪ 规划器标的


def test_marks_outside_the_catalogue_are_dropped(monkeypatch):
    monkeypatch.setattr(prop_marks, "ask_json",
                        lambda parts, schema, **kw: {"marks": [["幻激光枪"], []]})
    assert prop_marks.mark_props([dict(s) for s in SHOTS], PROPS) == {}


def test_a_judge_outage_marks_nothing_and_crashes_nothing(monkeypatch):
    def boom(parts, schema, **kw):
        raise RuntimeError("endpoint down")
    monkeypatch.setattr(prop_marks, "ask_json", boom)
    assert prop_marks.mark_props([dict(s) for s in SHOTS], PROPS) == {}


def test_no_props_no_call(monkeypatch):
    monkeypatch.setattr(prop_marks, "ask_json",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不该有调用")))
    assert prop_marks.mark_props([dict(s) for s in SHOTS], []) == {}
