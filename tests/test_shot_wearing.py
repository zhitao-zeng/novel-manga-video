"""Shot-level wearing state: the armour a stage puts on, takes off, or flies in uncrewed.

The chapter phase (phases.json "wears") says what a character wears for a span of chapters; it
cannot say 同一章中穿上、脱下、无人飞入另一套装甲.  A stage may now carry `wears`:

    {"wears": {"托尼·斯塔克": "马克2号"}}   put it on for this clip
    {"wears": {"托尼·斯塔克": null}}        bare for this clip, whatever the chapter says
    absent                                  the chapter phase decides, exactly as before

Three cases from the review, all asserted on one rule: a wearer and his armour are ONE person's
one stance - the armour is his appearance, not a second actor to face - and an uncrewed suit is a
moving prop that nobody stands opposite and that does not count as a face.
"""
from novel_manga.application.packing.assets import build_references
from novel_manga.models.bible import Character, Prop, StoryBible


def bible_with_armour():
    return StoryBible(
        novel_title="t", genre="g", visual_style="3d 国漫", palette="冷", style_fingerprint="fp",
        characters=[Character(name="托尼·斯塔克", role="主角", appearance="瘦高", wardrobe="便装"),
                    Character(name="席勒", role="男主角", appearance="清瘦", wardrobe="白大褂")],
        locations=["诊所：低矮房间"],
        props=[Prop(name="马克2号", category="战甲", appearance="银白色", first_chapter=1, quote="…"),
               Prop(name="马克3号", category="战甲", appearance="暗红色", first_chapter=1, quote="…")],
    )


BY_NAME = None
ON_DISK = {"prop_001": {"turnaround.jpeg"}, "prop_002": {"turnaround.jpeg"}}


def _pack(bible, cast, **kw):
    refs, bindings, loc = build_references(cast, "诊所", bible, {"诊所": "诊所：低矮房间"},
                                           props_index={p.name: p for p in bible.props},
                                           props_on_disk=ON_DISK, **kw)
    return refs, bindings, loc


def test_worn_overrides_seat_the_named_armour():
    """托尼 wears 马克2号 for this clip: the prop's card rides and the binding names the wearer."""
    bible = bible_with_armour()
    refs, bindings, loc = _pack(bible, ["托尼·斯塔克", "席勒"],
                                worn_overrides={"托尼·斯塔克": "马克2号"})
    prop_refs = [r for r in refs if r["role"] == "prop"]
    assert [r["name"] for r in prop_refs] == ["马克2号"]
    assert "托尼·斯塔克穿戴它时" in loc and "不占独立站位" in loc
    # 穿戴者是人物席位的同一个人：两张人物卡仍然只有两个人
    assert len([r for r in refs if r["role"] == "character"]) == 2


def test_wears_null_means_bare_for_this_clip():
    """A stage that takes the armour off must not fall back to the chapter default: no prop card
    rides, and the binding says nothing about wearing."""
    bible = bible_with_armour()
    refs, _, loc = _pack(bible, ["托尼·斯塔克"], worn_overrides={"托尼·斯塔克": None})
    assert not [r for r in refs if r["role"] == "prop"]
    assert "穿戴" not in loc


def test_unworn_armour_is_a_moving_object_not_a_face():
    """The second, uncrewed suit flies in: it is a prop with no wearer, and the binding says it
    is an object that moves on its own and does not add a person to the count."""
    bible = bible_with_armour()
    refs, _, loc = _pack(bible, ["托尼·斯塔克", "席勒"],
                         worn_overrides={"托尼·斯塔克": "马克2号"}, props=["马克3号"])
    # 道具位硬上限每 clip 一件：穿戴的优先（worn 在 seat_candidates 前列），无人套装本次不占位
    prop_refs = [r for r in refs if r["role"] == "prop"]
    assert [r["name"] for r in prop_refs] == ["马克2号"]
    assert "不占独立站位" in loc


def test_chapter_phase_still_decides_when_no_override():
    """Absent `wears`, the chapter phase is the word - the pre-existing behaviour, byte for byte."""
    from novel_manga.application.identity.phases import load_phases, phase_for
    bible = bible_with_armour()
    refs, _, loc = _pack(bible, ["托尼·斯塔克"])
    assert not [r for r in refs if r["role"] == "prop"]      # no phases.json here: nothing rides
    assert "穿戴" not in loc


def test_bare_this_clip_falls_back_to_the_base_card(tmp_path):
    """The chapter phase wears the armour; this clip took it off: the wearer's reference must be
    the BASE card, not the phase card that was drawn wearing it - showing the armour card while
    the stages say he is out of it draws the armour back on."""
    from novel_manga.application.identity.phases import POLICY
    bible = bible_with_armour()
    novel_dir = tmp_path / "nov"
    (novel_dir / "series_assets" / "characters").mkdir(parents=True)
    # a phase card and the base card exist on disk
    for asset in ("character_001", "character_001-p2"):
        directory = novel_dir / "series_assets" / "characters" / asset
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "turnaround.jpeg").write_bytes(b"jpeg")
    phases = {"托尼·斯塔克": [{"from": 1, "to": None, "asset_id": "character_001-p2",
                             "label": "穿甲", "wears": "马克2号"}]}
    import json as _json
    (novel_dir / "series_assets" / "phases.json").write_text(
        _json.dumps({"policy": POLICY, "characters": phases}), encoding="utf-8")

    # chapter default: the armour phase card + the prop rides
    refs, _, _ = _pack_with_dir(bible, ["托尼·斯塔克"], novel_dir, chapter=12, on_disk={"prop_001": {"turnaround.jpeg"}})
    assert refs[0]["asset_id"] == "character_001-p2"
    assert any(r["role"] == "prop" for r in refs)

    # this clip: bare - base card, no prop
    refs, _, loc = _pack_with_dir(bible, ["托尼·斯塔克"], novel_dir, chapter=12,
                                  on_disk={"prop_001": {"turnaround.jpeg"}}, worn_overrides={"托尼·斯塔克": None})
    assert refs[0]["asset_id"] == "character_001"            # 便装卡
    assert not [r for r in refs if r["role"] == "prop"]
    assert "穿戴" not in loc


def _pack_with_dir(bible, cast, novel_dir, *, chapter, on_disk, **kw):
    return build_references(cast, "诊所", bible, {"诊所": "诊所：低矮房间"},
                            novel_dir=novel_dir, chapter=chapter,
                            props_index={p.name: p for p in bible.props},
                            props_on_disk=on_disk, **kw)


def test_service_collects_shot_level_wears(tmp_path, monkeypatch):
    """The packer folds the stages' `wears` into a TIMELINE, not a last-wins value: on-then-off
    keeps the card riding (one request renders both moments), bare-below-bare stays bare."""
    from novel_manga.application.packing import service as packing_service
    captured = {}
    real_build = packing_service.build_references

    def spy(cast, location_short, bible, location_map, **kw):
        captured.update({k: kw.get(k) for k in ("worn_overrides", "props")})
        return real_build(cast, location_short, bible, location_map, **kw)

    monkeypatch.setattr(packing_service, "build_references", spy)

    def clip_with(wears_per_shot):
        return {"clip_id": "clip_01", "kind": "video", "location": "诊所", "seconds": 5,
                "shots": [{"index": i, "characters": ["托尼·斯塔克"], "turns": [], "wears": w, "props": []}
                          for i, w in enumerate(wears_per_shot, 1)]}

    ctx = {"episode_dir": tmp_path / "nov" / "nov_1", "location_map": {"诊所": "诊所：低矮房间"},
           "bible": bible_with_armour(), "identity_data": None, "body_refs": None,
           "grammar": None, "frame": None, "compiler_options": None, "overrides": {}}
    for shot in clip_with([{"托尼·斯塔克": "马克2号"}]).get("shots", []):
        pass

    # 穿上→脱下：卡随行（一个请求拍两个时刻），绑定叙述变化
    try:
        packing_service.clip_entry(clip_with([{"托尼·斯塔克": "马克2号"}, {"托尼·斯塔克": None}]), "clip_01", ctx, None)
    except Exception:
        pass
    assert captured["worn_overrides"] == {"托尼·斯塔克": "马克2号"}

    # 整段不穿：不随行
    try:
        packing_service.clip_entry(clip_with([{"托尼·斯塔克": None}, {"托尼·斯塔克": None}]), "clip_01", ctx, None)
    except Exception:
        pass
    assert captured["worn_overrides"] == {"托尼·斯塔克": None}

    # 穿到底：随行
    try:
        packing_service.clip_entry(clip_with([{"托尼·斯塔克": "马克2号"}, {"托尼·斯塔克": "马克2号"}]), "clip_01", ctx, None)
    except Exception:
        pass
    assert captured["worn_overrides"] == {"托尼·斯塔克": "马克2号"}
