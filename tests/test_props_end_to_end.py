"""The whole chain in one run, with the outside world stubbed: read (extract) → bible grows →
card built (fake provider) → plan schema names the prop → packing seats its reference."""
import json

import pytest

from novel_manga.application.review import bible as review_bible
from novel_manga.models.bible import StoryBible


def test_prop_chain_end_to_end(tmp_path, monkeypatch):
    novel = tmp_path / "book"
    novel.mkdir()
    (novel / "story_bible.json").write_text(json.dumps({
        "novel_title": "测试", "genre": "通用", "visual_style": "3d 国漫", "palette": "冷",
        "style_fingerprint": "fp",
        "characters": [{"name": "莱恩", "appearance": "瘦高", "wardrobe": "黑大衣"}],
        "locations": ["事务所：临街小屋"]}, ensure_ascii=False), encoding="utf-8")
    (novel / "profile.json").write_text(json.dumps({"style": "3d", "frame": "9:16", "props": True}),
                                        encoding="utf-8")

    # 1) 读书：提取一路走到 bible.props 与生长记录（模型外部调用 stub）
    monkeypatch.setattr(review_bible, "extract_names", lambda text: [])
    monkeypatch.setattr(review_bible, "extract_locations", lambda text, known: [])
    def fake_extract(text, known_props, known_names):
        return [{"name": "青铜短剑", "category": "武器", "appearance": "剑身泛青、蟠螭纹",
                 "material": "青铜", "owner": "莱恩", "quote": "抽出那柄青铜短剑",
                 "closeup": True, "wearable": False}]
    monkeypatch.setattr(review_bible, "extract_props", fake_extract)
    result = review_bible.grow_bible(novel, "他抽出那柄青铜短剑。", 1)
    assert result["props"] == ["青铜短剑"]
    bible = StoryBible.model_validate_json((novel / "story_bible.json").read_text(encoding="utf-8"))
    assert bible.props[0].closeup is True

    # 2) 建卡：假 provider 走真 build_selected，道具卡与 detail 落盘、manifest 有 props 段
    from novel_manga.media.asset_builder import FramedAssetFactory
    from novel_manga.config import Settings
    from PIL import Image
    class Provider:
        def create_image(self, prompt, output, **kwargs):
            output.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (64, 64), "gray").save(output)
            from novel_manga.providers.phanrouter_images import ImageResult
            return ImageResult(path=output)
    factory = FramedAssetFactory(Settings(reuse_existing_assets=True), Provider())
    manifest = factory.build_selected(novel / "series_assets", bible, set(), set(), prop_ids={"prop_001"})
    assert (novel / "series_assets/props/prop_001/turnaround.jpeg").is_file()
    assert (novel / "series_assets/props/prop_001/detail.jpeg").is_file()   # closeup=True
    assert [p.asset_id for p in manifest.props] == ["prop_001"]

    # 3) 规划 schema 带道具枚举
    from novel_manga.planning.contracts import build_schema
    from novel_manga.planning.context import PlannerContext
    schema = build_schema(["莱恩"], ["事务所"], ["s1"], ctx=PlannerContext(), prop_names=["青铜短剑"])
    stage = schema["properties"]["clips"]["items"]["properties"]["stages"]["items"]["properties"]
    assert stage["props"]["items"]["enum"] == ["青铜短剑"]

    # 4) 装配：clip 点名道具 → 道具座位落在 detail.jpeg（closeup），绑定跟场景行
    from novel_manga.application.packing.assets import build_references
    refs, bindings, loc = build_references(["莱恩"], "事务所", bible, {"事务所": "事务所：临街小屋"},
                                           novel_dir=novel, chapter=1, props=["青铜短剑"])
    seats = [(r["role"], r["path"]) for r in refs]
    assert ("prop", "series_assets/props/prop_001/detail.jpeg") in seats
    assert not any("青铜短剑" in b for b in bindings) and "青铜短剑" in loc
