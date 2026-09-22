"""Prop model: defaults, and a bible without the props key loads exactly as before."""
from novel_manga.models.bible import Prop, StoryBible


def test_prop_defaults():
    p = Prop(name="青铜短剑", category="武器", appearance="剑身泛青", first_chapter=12,
             quote="他抽出那柄青铜短剑")
    assert p.material == "" and p.owner == "" and p.closeup is False and p.wearable is False


def test_bible_without_props_key_loads_unchanged():
    # 老书 story_bible.json 没有 props 键：加载后 props 为空列表
    raw = {"novel_title": "雾月秘典", "genre": "gaslamp", "visual_style": "3d 国漫",
           "palette": "冷", "style_fingerprint": "abc",
           "characters": [{"name": "莱恩", "appearance": "瘦高", "wardrobe": "黑大衣"}],
           "locations": ["事务所"]}
    bible = StoryBible.model_validate(raw)
    assert bible.props == []


def test_bible_with_props_roundtrip():
    raw = {"novel_title": "t", "genre": "g", "visual_style": "s", "palette": "p",
           "style_fingerprint": "f",
           "props": [{"name": "玄天宝录", "category": "法器", "appearance": "玉册",
                      "first_chapter": 3, "quote": "玄天宝录", "closeup": True}]}
    bible = StoryBible.model_validate(raw)
    assert bible.props[0].closeup is True
    assert StoryBible.model_validate(bible.model_dump()).props[0].name == "玄天宝录"
