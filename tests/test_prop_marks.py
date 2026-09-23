"""The judge's post-plan prop marking: one call per chapter, unioned with any planner marks.

The pilot showed the planner's model (flashnext) answering the optional props field empty on
every stage while the judge (27B) marked the acquisition shot correctly.  So marking rides the
judge channel, after validation, before the chapter script is written.
"""
from novel_manga.application.planning import prop_marks
from novel_manga.models.bible import Prop


PROPS = [Prop(name="功法玉简", category="法器", appearance="金色玉石", first_chapter=1411, quote="…"),
         Prop(name="冰霜之刃", category="武器", appearance="", first_chapter=1411, quote="…")]


def _shots():
    return [
        {"origin_index": 1, "motion_prompt": "沈玄川伸手凌空一抓，幽光化作玉石落入掌心", "visual_prompt": ""},
        {"origin_index": 2, "motion_prompt": "他走回宿舍躺下", "visual_prompt": ""},
    ]


def test_judge_marks_and_planner_marks_unite(monkeypatch):
    monkeypatch.setattr(prop_marks, "ask_json",
                        lambda parts, schema, **kw: {"marks": [{"shot": 1, "props": ["功法玉简"]}]})
    shots = _shots()
    shots[1]["props"] = ["冰霜之刃"]                    # 规划器自己标了一个
    marked = prop_marks.mark_props(shots, PROPS)
    assert marked == {1: ["功法玉简"], 2: ["冰霜之刃"]}  # 判官标的 ∪ 规划器标的，键是镜头位置


def test_a_split_stages_mark_lands_on_one_sibling_only(monkeypatch):
    """Two shots from one original stage share origin_index 2; only the second shows the prop.

    Keying by origin_index would seat the sword's card on both siblings - and, written back the
    old way, on every shot of that stage in the script.
    """
    monkeypatch.setattr(prop_marks, "ask_json",
                        lambda parts, schema, **kw: {"marks": [{"shot": 3, "props": ["冰霜之刃"]}]})
    shots = [{"origin_index": 1, "motion_prompt": "沈玄川站在原地看着他", "visual_prompt": ""},
             {"origin_index": 2, "motion_prompt": "沈玄川说话", "visual_prompt": "",
              "turns": [{"speaker_name": "沈玄川", "text": "……", "delivery_mode": "visible_dialogue"}]},
             {"origin_index": 2, "motion_prompt": "沈玄川抽出冰霜之刃指向门口", "visual_prompt": ""}]
    marked = prop_marks.mark_props(shots, PROPS)
    assert marked == {3: ["冰霜之刃"]}                 # 只标第二镜，兄弟镜不沾


def test_out_of_range_or_garbled_numbers_keep_only_planner_marks(monkeypatch):
    monkeypatch.setattr(prop_marks, "ask_json",
                        lambda parts, schema, **kw: {"marks": [
                            {"shot": 99, "props": ["功法玉简"]},   # 名单外编号
                            {"shot": "x", "props": ["冰霜之刃"]},   # 不是数字
                            {"shot": 1, "props": []},              # 空数组
                        ]})
    shots = _shots()
    shots[0]["props"] = ["功法玉简"]                     # 判官全废，规划器自己的标注仍在
    assert prop_marks.mark_props(shots, PROPS) == {1: ["功法玉简"]}


def test_duplicate_numbers_from_the_judge_union_in_place(monkeypatch):
    monkeypatch.setattr(prop_marks, "ask_json",
                        lambda parts, schema, **kw: {"marks": [
                            {"shot": 1, "props": ["功法玉简"]},
                            {"shot": 1, "props": ["冰霜之刃"]},     # 同镜两条：并起来，不覆盖
                        ]})
    assert prop_marks.mark_props(_shots(), PROPS) == {1: ["功法玉简", "冰霜之刃"]}


def test_marks_outside_the_catalogue_are_dropped(monkeypatch):
    monkeypatch.setattr(prop_marks, "ask_json",
                        lambda parts, schema, **kw: {"marks": [{"shot": 1, "props": ["幻激光枪"]}]})
    assert prop_marks.mark_props(_shots(), PROPS) == {}


def test_a_judge_outage_marks_nothing_and_crashes_nothing(monkeypatch):
    def boom(parts, schema, **kw):
        raise RuntimeError("endpoint down")
    monkeypatch.setattr(prop_marks, "ask_json", boom)
    assert prop_marks.mark_props(_shots(), PROPS) == {}


def test_no_props_no_call(monkeypatch):
    monkeypatch.setattr(prop_marks, "ask_json",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不该有调用")))
    assert prop_marks.mark_props(_shots(), []) == {}
