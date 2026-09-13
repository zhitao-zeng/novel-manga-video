"""A shot's cast is completed from its own description: the given name, the name without a title and
小+name all count; two-character common-noun names never do; the repair script fixes storyboards on disk."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import complete_cast_thin  # noqa: E402
import plan_chapter_thin  # noqa: E402
from novel_manga.models import Character, StoryBible  # noqa: E402

EVERYONE = ["莱恩·格雷", "薇奥拉公主", "琥珀·高德", "艾琳娜", "灵魂", "秘女", "船长", "神"]


def test_short_forms():
    assert {"薇奥拉", "小薇奥拉"} <= plan_chapter_thin.short_forms("薇奥拉公主")
    assert {"琥珀", "小琥珀"} <= plan_chapter_thin.short_forms("琥珀·高德")
    assert plan_chapter_thin.short_forms("船长") == set()
    assert plan_chapter_thin.short_forms("神") == set()
    assert "薇奥拉公主" not in plan_chapter_thin.short_forms("薇奥拉公主")


def test_mentioned_characters_in_order_and_without_common_nouns():
    text = "薇奥拉环着莱恩的脖子，小琥珀卧在窗台上，两人的灵魂靠近，船长在门外"
    assert plan_chapter_thin.mentioned_characters(text, EVERYONE) == ["薇奥拉公主", "莱恩·格雷", "琥珀·高德"]


def test_complete_characters_adds_the_described_and_keeps_the_cap():
    shot = {"visual_prompt": "薇奥拉环着莱恩的脖子，两人距离很近。", "motion_prompt": "琥珀猫卧在窗台上。"}
    cast, added = plan_chapter_thin.complete_characters(["莱恩·格雷", "琥珀·高德"], shot, EVERYONE)
    assert added == ["薇奥拉公主"] and cast == ["莱恩·格雷", "琥珀·高德", "薇奥拉公主"]
    full = ["a·b", "c·d", "e·f", "g·h", "i·j", "k·l"]
    cast, added = plan_chapter_thin.complete_characters(full, shot, EVERYONE)
    assert cast == full and added == []


def test_aliases_from_the_novel_count_too(monkeypatch):
    monkeypatch.setitem(plan_chapter_thin.ALIASES, "作家小姐", "艾琳娜")
    assert plan_chapter_thin.mentioned_characters("作家小姐有些不满", EVERYONE) == ["艾琳娜"]


def test_repair_script_completes_storyboards_on_disk(tmp_path, monkeypatch):
    novel = tmp_path / "wuyue"
    episode = novel / "wuyue_761"
    episode.mkdir(parents=True)
    bible = StoryBible(novel_title="雾月", genre="gaslamp", visual_style="3d", palette="p", style_fingerprint="f",
                       characters=[Character(name=n, appearance="x", wardrobe="y") for n in EVERYONE])
    (novel / "story_bible.json").write_text(bible.model_dump_json(), encoding="utf-8")
    script = {"shots": [
        {"segment_id": "seg_4", "characters": ["莱恩·格雷", "琥珀·高德"], "visual_prompt": "薇奥拉环着莱恩的脖子", "motion_prompt": ""},
        {"segment_id": "seg_5", "characters": ["莱恩·格雷"], "visual_prompt": "莱恩独自坐着", "motion_prompt": ""}]}
    (episode / "chapter_script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["complete_cast_thin.py", "--novel-dir", str(novel)])
    assert complete_cast_thin.main() == 0
    assert json.loads((episode / "chapter_script.json").read_text())["shots"][0]["characters"] == ["莱恩·格雷", "琥珀·高德"]  # dry run
    monkeypatch.setattr(sys, "argv", ["complete_cast_thin.py", "--novel-dir", str(novel), "--apply", "--no-pack"])
    assert complete_cast_thin.main() == 0
    fixed = json.loads((episode / "chapter_script.json").read_text())["shots"]
    assert fixed[0]["characters"] == ["莱恩·格雷", "琥珀·高德", "薇奥拉公主"] and fixed[1]["characters"] == ["莱恩·格雷"]
    assert (episode / "chapter_script.json.bak-cast").is_file()
    assert (novel / "cast_completion_targets.txt").read_text() == "761"
