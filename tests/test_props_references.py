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
    assert not any("青铜短剑" in b for b in bindings)            # 物品不混进人物绑定行
    assert "青铜短剑" in loc and "它是物品，不是人物" in loc     # 跟着场景行走


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


def _location_seat(refs) -> str:
    return next(r["tag"] for r in refs if r["role"] == "location")


def test_location_binding_names_the_locations_own_picture():
    """With a prop seated after the location, the scene text still points at the location's seat.

    @图片1 莱恩, @图片2 事务所, @图片3 青铜短剑: the binding used to say "@图片3用于<事务所>…"
    because it was written after the prop had taken its number.
    """
    bible = _bible()
    _, by_name = _index(bible)
    refs, _, loc = build_references(["莱恩"], "事务所", bible, {"事务所": "事务所：临街小屋"},
                                    props=["青铜短剑"], props_index=by_name,
                                    props_on_disk={"prop_001": {"turnaround.jpeg"}})
    assert loc.startswith(_location_seat(refs))                # 指向 role=location 那条，不是道具
    assert "<青铜短剑>对应@图片3" in loc                        # 道具的座位号与它的 reference 一致


def test_location_binding_holds_for_every_seat_layout():
    """The binding names the location's picture across the four reference layouts a clip can have."""
    bible = _bible()
    _, by_name = _index(bible)

    def binding_of(**kw):
        refs, _, loc = build_references(["莱恩"], "事务所", bible, {"事务所": "事务所：临街小屋"},
                                        props_index=by_name, **kw)
        return _location_seat(refs), loc

    # 无道具
    seat, loc = binding_of()
    assert loc.startswith(seat)
    # 一件道具（地点后追加）
    seat, loc = binding_of(props=["青铜短剑"], props_on_disk={"prop_001": {"turnaround.jpeg"}})
    assert loc.startswith(seat)
    # 人物双视图（地点被推后两位）
    seat, loc = binding_of(novel_dir=None)  # 旧规则：无 novel_dir 时 phase 为 None 也补表情图
    assert loc.startswith(seat)
    # 带声音参考（音频不占图片编号）
    class S:
        two_views = "all"
        two_view_cast_limit = 2
        voices = {"莱恩": "series_assets/voices/莱恩.wav"}
    refs, _, loc = build_references(["莱恩"], "事务所", bible, {"事务所": "事务所：临街小屋"},
                                    speakers=("莱恩",), settings=S(),
                                    props=["青铜短剑"], props_index=by_name,
                                    props_on_disk={"prop_001": {"turnaround.jpeg"}})
    assert loc.startswith(_location_seat(refs))
    assert [r["tag"] for r in refs if r["role"] == "voice"] == ["@音频1"]
