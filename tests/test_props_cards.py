"""Prop cards: the single-object prompt, the spec, and the manifest's props section."""
from novel_manga.media.asset_prompts import prop_prompt
from novel_manga.media.asset_records import merge_manifest
from novel_manga.models.bible import Prop, StoryBible


def _bible():
    return StoryBible(novel_title="t", genre="g", visual_style="高精度半写实3D国漫CG",
                      palette="冷蓝", style_fingerprint="abc123", characters=[], locations=[])


def test_prop_prompt_is_single_object_clean_plate():
    p = Prop(name="青铜短剑", category="武器", appearance="剑身泛青、蟠螭纹", material="青铜",
             first_chapter=1, quote="他抽出那柄青铜短剑", closeup=True)
    text = prop_prompt(_bible(), p, family="3d")
    assert "青铜短剑" in text and "蟠螭纹" in text and "青铜" in text
    assert "道具资产" in text
    assert "不得出现人物" in text            # 与地点卡同款的空场规则
    assert "abc123" in text                  # 指纹默认进提示词（与人物卡一致）


def test_prop_prompt_without_material_skips_the_clause():
    p = Prop(name="木盒", category="工具", appearance="方形旧木盒", first_chapter=1, quote="…")
    text = prop_prompt(_bible(), p, family="3d")
    assert "；材质：" not in text          # 道具自己的材质分句不出现（渲染指引里的材质一词无关）


def test_manifest_merges_props(tmp_path):
    root = tmp_path / "series_assets"
    root.mkdir()
    record = {"asset_id": "prop_001", "kind": "prop", "name": "青铜短剑",
              "identity_invariants": ["剑身泛青"], "state_variables": {},
              "reference_scope": {"inherit": [], "exclude": []},
              "spec_path": "series_assets/props/prop_001/spec.json",
              "primary_image": "series_assets/props/prop_001/turnaround.jpeg",
              "secondary_image": None, "prompt_sha256": "x"}
    manifest = merge_manifest(root, "fp", {}, {}, {}, props={"prop_001": record})
    assert manifest.props[0].asset_id == "prop_001"
    # 再合并一次别的东西，props 段要留存
    manifest = merge_manifest(root, "fp", {}, {}, {}, props=None)
    assert [p.asset_id for p in manifest.props] == ["prop_001"]
