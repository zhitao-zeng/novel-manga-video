"""Prop extraction: the merge pilot's evidence rules on the judge's channel, opt-in per book."""
import json

from novel_manga.application.review import bible as review_bible


def test_extract_props_rules(monkeypatch):
    asked = {}

    def fake_ask(parts, schema, **kw):
        asked["settings"] = kw.get("settings")
        return {"props": [
            {"name": "青铜短剑", "category": "武器", "appearance": "剑身泛青",
             "first_chapter": 1, "quote": "抽出那柄青铜短剑", "closeup": False, "wearable": False},
            {"name": "茶杯", "category": "工具", "appearance": "白瓷",
             "first_chapter": 1, "quote": "原文里没有这四个字", "closeup": False, "wearable": False},
        ]}

    monkeypatch.setattr(review_bible.model_client, "ask_json", fake_ask)
    text = "他抽出那柄青铜短剑，又端起茶杯喝了一口。"
    rows = review_bible.extract_props(text, [], {"莱恩"})
    assert [r["name"] for r in rows] == ["青铜短剑"]   # quote 不是原文连续子串的被丢弃
    assert asked["settings"] is not None and asked["settings"].model == "Qwen3.8-27B-Project"


def test_extract_props_refuses_a_name_taken_by_a_character(monkeypatch):
    monkeypatch.setattr(review_bible.model_client, "ask_json",
                        lambda parts, schema, **kw: {"props": [
                            {"name": "玄天", "category": "法器", "appearance": "剑",
                             "first_chapter": 1, "quote": "玄天", "closeup": False, "wearable": False}]})
    assert review_bible.extract_props("玄天出鞘。", [], {"玄天"}) == []   # 与人物同名：拒收


def test_scan_chapter_without_prop_scan_makes_no_prop_call(monkeypatch):
    calls = []
    monkeypatch.setattr(review_bible.model_client, "ask_json",
                        lambda parts, schema, **kw: calls.append(kw.get("name")) or {"characters": [], "locations": [], "props": []})
    out = review_bible.scan_chapter("一段原文。", [], names=True)
    assert out["props"] == []
    assert "props" not in calls                          # 老路径：道具这一路根本没发问


def _novel(tmp_path, *, props_flag):
    novel = tmp_path / "book"
    novel.mkdir(parents=True)
    (novel / "story_bible.json").write_text(json.dumps({
        "novel_title": "测试", "genre": "通用", "visual_style": "s", "palette": "p",
        "style_fingerprint": "fp",
        "characters": [{"name": "莱恩", "appearance": "瘦高", "wardrobe": "黑大衣"}],
        "locations": []}, ensure_ascii=False), encoding="utf-8")
    (novel / "profile.json").write_text(json.dumps({"props": props_flag}), encoding="utf-8")
    return novel


def test_growth_commits_props_only_when_the_book_opts_in(tmp_path, monkeypatch):
    monkeypatch.setattr(review_bible, "extract_names", lambda text: [])
    monkeypatch.setattr(review_bible, "extract_locations", lambda text, known: [])
    monkeypatch.setattr(review_bible, "extract_props", lambda text, known_props, known_names: [
        {"name": "青铜短剑", "category": "武器", "appearance": "剑身泛青", "material": "青铜",
         "owner": "莱恩", "first_chapter": 1, "quote": "抽出那柄青铜短剑", "closeup": False, "wearable": False}])

    on = _novel(tmp_path / "on", props_flag=True)
    result = review_bible.grow_bible(on, "他抽出那柄青铜短剑。", 1)
    bible = json.loads((on / "story_bible.json").read_text(encoding="utf-8"))
    assert [p["name"] for p in bible["props"]] == ["青铜短剑"]
    growth = json.loads((on / "bible_growth.json").read_text(encoding="utf-8"))
    assert growth["1"]["props"] == ["青铜短剑"]
    assert result["props"] == ["青铜短剑"]

    off = _novel(tmp_path / "off", props_flag=False)
    review_bible.grow_bible(off, "他抽出那柄青铜短剑。", 1)
    bible = json.loads((off / "story_bible.json").read_text(encoding="utf-8"))
    assert "props" not in bible                       # 未准入的书：不写 props 键、不提取
    growth = json.loads((off / "bible_growth.json").read_text(encoding="utf-8"))
    assert "props" not in growth["1"]
