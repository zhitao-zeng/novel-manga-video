"""The prop seat in a clip's references: one seat after the location, detail for closeups,
unknown names dropped, and no props at all is byte-identical to before."""
from novel_manga.application.packing.assets import build_references
from novel_manga.models.bible import Character, Prop, StoryBible


def _bible():
    return StoryBible(
        novel_title="t", genre="g", visual_style="3d 国漫", palette="冷", style_fingerprint="fp",
        characters=[Character(name="莱恩", role="主角", appearance="瘦高", wardrobe="黑大衣")],
        locations=["事务所：临街小屋"],
        props=[Prop(name="青铜短剑", category="武器", appearance="泛青", first_chapter=1, quote="…"),
               Prop(name="玄天宝录", category="法器", appearance="玉册", first_chapter=1, quote="…",
                    closeup=True)],
    )


def _index(bible):
    return {p.name: i for i, p in enumerate(bible.props, start=1)}, {p.name: p for p in bible.props}


def test_prop_takes_one_seat_after_the_location():
    bible = _bible()
    index, by_name = _index(bible)
    refs, bindings, loc = build_references(["莱恩"], "事务所", bible, {"事务所": "事务所：临街小屋"},
                                           props=["青铜短剑"], props_index=by_name,
                                           props_on_disk={"prop_001": {"turnaround.jpeg"}})
    seats = [(r["role"], r["path"]) for r in refs]
    assert ("prop", "series_assets/props/prop_001/turnaround.jpeg") in seats
    assert len([r for r in refs if r["role"] == "prop"]) == 1     # 道具位 ≤1
    assert seats.index(("prop", "series_assets/props/prop_001/turnaround.jpeg")) > \
        seats.index(("location", "series_assets/locations/location_001/establishing.jpeg"))
    assert any("青铜短剑" in b for b in bindings)


def test_closeup_prop_uses_detail_image():
    bible = _bible()
    _, by_name = _index(bible)
    refs, _, _ = build_references(["莱恩"], "事务所", bible, {"事务所": "事务所：临街小屋"},
                                  props=["玄天宝录"], props_index=by_name,
                                  props_on_disk={"prop_002": {"detail.jpeg"}})
    assert any(r["path"] == "series_assets/props/prop_002/detail.jpeg" for r in refs)


def test_unknown_or_missing_prop_is_dropped_not_invented(caplog=None):
    bible = _bible()
    _, by_name = _index(bible)
    refs, _, _ = build_references(["莱恩"], "事务所", bible, {"事务所": "事务所：临街小屋"},
                                  props=["幻激光枪"],                 # 名单外（幻觉）
                                  props_index=by_name,
                                  props_on_disk={})                   # 卡也没建
    assert not [r for r in refs if r["role"] == "prop"]


def test_no_props_is_byte_identical_to_before():
    bible = _bible()
    refs, bindings, loc = build_references(["莱恩"], "事务所", bible, {"事务所": "事务所：临街小屋"})
    assert not [r for r in refs if r["role"] == "prop"]
    assert not any("不放大、不缩小" in b for b in bindings)   # 道具座位的绑定语不存在
    refs2, bindings2, loc2 = build_references(["莱恩"], "事务所", bible, {"事务所": "事务所：临街小屋"},
                                              props=[], props_index={}, props_on_disk={})
    assert (refs, bindings, loc) == (refs2, bindings2, loc2)
