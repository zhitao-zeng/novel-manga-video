"""A wearable prop anchors its own card; a phase wearing it carries both cards, and a wears
pointing nowhere changes nothing."""
import json
from pathlib import Path

from novel_manga.application.identity.phases import wearable_prop
from novel_manga.application.packing.assets import build_references
from novel_manga.models.bible import Character, Prop, StoryBible


def _bible():
    return StoryBible(
        novel_title="t", genre="g", visual_style="s", palette="p", style_fingerprint="fp",
        characters=[Character(name="托尼", role="主角", appearance="黑发", wardrobe="衬衫")],
        locations=["厂房：钢结构"],
        props=[Prop(name="Mark XLII 战甲", category="法器", appearance="红金配色装甲", material="合金",
                    first_chapter=50, quote="…", wearable=True)],
    )


def test_wearable_prop_lookup():
    bible = _bible()
    phase = {"from": 50, "to": None, "asset_id": "character_001-p2", "wears": "Mark XLII 战甲"}
    prop, asset = wearable_prop(phase, bible)
    assert prop.name == "Mark XLII 战甲" and asset == "prop_001"
    assert wearable_prop({"from": 1, "asset_id": "x"}, bible) is None          # 没有 wears 键
    assert wearable_prop({"wears": "不存在的甲"}, bible) is None                 # 指向不存在的道具


def test_phase_wearing_a_prop_carries_both_cards(tmp_path):
    bible = _bible()
    novel = tmp_path / "book"
    cards = novel / "series_assets"
    (cards / "characters" / "character_001-p2").mkdir(parents=True)
    (cards / "characters" / "character_001-p2" / "turnaround.jpeg").write_bytes(b"\xff")
    (cards / "props" / "prop_001").mkdir(parents=True)
    (cards / "props" / "prop_001" / "turnaround.jpeg").write_bytes(b"\xff")
    (cards / "phases.json").write_text(json.dumps({
        "characters": {"托尼": [{"from": 50, "to": None, "asset_id": "character_001-p2",
                                 "wears": "Mark XLII 战甲"}]}}), encoding="utf-8")

    refs, _, _ = build_references(["托尼"], "厂房", bible, {"厂房": "厂房：钢结构"},
                                  novel_dir=novel, chapter=60)
    seats = {(r["role"], r["path"]) for r in refs}
    assert ("character", "series_assets/characters/character_001-p2/turnaround.jpeg") in seats
    assert ("prop", "series_assets/props/prop_001/turnaround.jpeg") in seats

    # 同一章但 phase 没有 wears（换一本无联动书的行为）：只有角色卡
    (cards / "phases.json").write_text(json.dumps({
        "characters": {"托尼": [{"from": 50, "to": None, "asset_id": "character_001-p2"}]}}),
        encoding="utf-8")
    refs2, _, _ = build_references(["托尼"], "厂房", bible, {"厂房": "厂房：钢结构"},
                                   novel_dir=novel, chapter=60)
    assert not [r for r in refs2 if r["role"] == "prop"]


def test_wears_pointing_nowhere_is_skipped(tmp_path):
    bible = _bible()
    novel = tmp_path / "book"
    cards = novel / "series_assets"
    (cards / "characters" / "character_001-p2").mkdir(parents=True)
    (cards / "characters" / "character_001-p2" / "turnaround.jpeg").write_bytes(b"\xff")
    (cards / "phases.json").write_text(json.dumps({
        "characters": {"托尼": [{"from": 50, "to": None, "asset_id": "character_001-p2",
                                 "wears": "银武士"}]}}), encoding="utf-8")   # 圣经里没有这个道具
    refs, _, _ = build_references(["托尼"], "厂房", bible, {"厂房": "厂房：钢结构"},
                                  novel_dir=novel, chapter=60)
    assert not [r for r in refs if r["role"] == "prop"]
    assert any(r["role"] == "character" for r in refs)
