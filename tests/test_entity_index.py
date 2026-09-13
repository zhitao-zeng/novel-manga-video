"""The entity index records how the book really calls each character; name lookups go through it."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_entity_index  # noqa: E402
import plan_chapter_thin  # noqa: E402


def novel(tmp_path: Path) -> Path:
    root = tmp_path / "wuyue"
    bible = {"characters": [
        {"name": "莱恩·格雷", "role": "主角"}, {"name": "薇奥拉公主", "role": "公主"}, {"name": "琥珀·高德", "role": "独立角色"},
        {"name": "约翰·华生", "role": "医生"}, {"name": "约翰·邓恩教授", "role": "教授"}, {"name": "酒保", "role": "龙套"},
        {"name": "赫尔曼", "role": ""}, {"name": "赫尔男爵", "role": ""}]}
    (root / "wuyue_1").mkdir(parents=True)
    (root / "wuyue_2").mkdir()
    (root / "story_bible.json").write_text(json.dumps(bible, ensure_ascii=False), encoding="utf-8")
    (root / "bible_aliases.json").write_text(json.dumps({"作家小姐": "薇奥拉公主"}, ensure_ascii=False), encoding="utf-8")
    (root / "wuyue_1" / "segments.json").write_text(json.dumps([
        {"segment_id": "seg_1", "text": "薇奥拉公主走进来，莱恩抱着小琥珀。作家小姐笑了。约翰走了。赫尔曼在门口。"}], ensure_ascii=False), encoding="utf-8")
    (root / "wuyue_2" / "segments.json").write_text(json.dumps([
        {"segment_id": "seg_1", "text": "薇奥拉看着莱恩·格雷。酒保端来酒。约翰·华生到了。"}], ensure_ascii=False), encoding="utf-8")
    return root


def test_index_keeps_only_forms_the_book_uses_and_that_point_at_one_person(tmp_path):
    index = build_entity_index.build(novel(tmp_path))
    rows = {c["name"]: c for c in index["characters"]}
    assert set(rows["薇奥拉公主"]["forms"]) == {"薇奥拉公主", "薇奥拉", "作家小姐"}
    assert set(rows["琥珀·高德"]["forms"]) == {"小琥珀"}                   # the book says 小琥珀; longest match counts it once
    assert set(rows["莱恩·格雷"]["forms"]) == {"莱恩·格雷", "莱恩"}
    assert "约翰" not in rows["约翰·华生"]["forms"] and "约翰" in index["ambiguous_forms"]
    assert rows["赫尔曼"]["forms"] == {"赫尔曼": 1} and rows["赫尔男爵"]["forms"] == {}  # 赫尔 inside 赫尔曼 is not a mention
    assert rows["莱恩·格雷"]["mentions"] == 2                                             # 莱恩 + 莱恩·格雷, counted once each
    assert rows["酒保"]["generic"] and rows["莱恩·格雷"]["tier"] == "lead"
    assert rows["薇奥拉公主"]["first_chapter"] == 1 and rows["薇奥拉公主"]["last_chapter"] == 2
    assert rows["约翰·邓恩教授"]["forms"] == {} and rows["约翰·邓恩教授"]["first_chapter"] is None


def test_name_lookups_go_through_the_index_when_present(tmp_path, monkeypatch):
    root = novel(tmp_path)
    index = build_entity_index.build(root)
    (root / "entity_index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    assert plan_chapter_thin.load_entity_index(root)
    try:
        assert plan_chapter_thin.name_forms("薇奥拉公主") == {"薇奥拉公主", "薇奥拉", "作家小姐"}
        text = "作家小姐环着莱恩的脖子，小琥珀在窗台上，约翰在门外"
        everyone = [c["name"] for c in index["characters"]]
        assert plan_chapter_thin.mentioned_characters(text, everyone) == ["薇奥拉公主", "莱恩·格雷", "琥珀·高德"]
    finally:
        plan_chapter_thin.ENTITY_FORMS.clear()
        plan_chapter_thin.ENTITY_TIERS.clear()
        plan_chapter_thin._FORMS_INDEX.clear()
    assert not plan_chapter_thin.load_entity_index(tmp_path / "nowhere")
    assert "小薇奥拉" in plan_chapter_thin.name_forms("薇奥拉公主")  # back to derived forms without an index
