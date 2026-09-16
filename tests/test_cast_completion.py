"""A shot's cast is completed from its own description: the given name, the name without a title and
小+name all count; two-character common-noun names never do; the repair script fixes storyboards on disk."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import complete_cast_thin  # noqa: E402
import novel_manga.planning.cast as pc_cast
import novel_manga.story.identity as pc_identity
from novel_manga.planning.context import PlannerContext  # noqa: E402
from novel_manga.models import Character, StoryBible  # noqa: E402

EVERYONE = ["莱恩·格雷", "薇奥拉公主", "琥珀·高德", "艾琳娜", "灵魂", "秘女", "船长", "神"]


def test_short_forms():
    assert {"薇奥拉", "小薇奥拉"} <= pc_identity.short_forms("薇奥拉公主")
    assert {"琥珀", "小琥珀"} <= pc_identity.short_forms("琥珀·高德")
    assert pc_identity.short_forms("船长") == set()
    assert pc_identity.short_forms("神") == set()
    assert "薇奥拉公主" not in pc_identity.short_forms("薇奥拉公主")


def test_mentioned_characters_in_order_and_without_common_nouns():
    planner_ctx = PlannerContext.from_env()
    text = "薇奥拉环着莱恩的脖子，小琥珀卧在窗台上，两人的灵魂靠近，船长在门外"
    assert pc_cast.mentioned_characters(text, EVERYONE, ctx=planner_ctx) == ["薇奥拉公主", "莱恩·格雷", "琥珀·高德"]


def test_complete_characters_adds_the_described_and_keeps_the_cap():
    planner_ctx = PlannerContext.from_env()
    shot = {"visual_prompt": "薇奥拉环着莱恩的脖子，两人距离很近。", "motion_prompt": "琥珀猫卧在窗台上。"}
    cast, added = pc_cast.complete_characters(["莱恩·格雷", "琥珀·高德"], shot, EVERYONE, ctx=planner_ctx)
    assert added == ["薇奥拉公主"] and cast == ["莱恩·格雷", "琥珀·高德", "薇奥拉公主"]
    full = ["a·b", "c·d", "e·f", "g·h", "i·j", "k·l"]
    cast, added = pc_cast.complete_characters(full, shot, EVERYONE, ctx=planner_ctx)
    assert cast == full and added == []


def test_shared_or_nested_short_forms_never_add_the_wrong_person():
    planner_ctx = PlannerContext.from_env()
    people = ["约翰·华生", "约翰·邓恩教授", "赫尔曼", "赫尔男爵", "莱恩·格雷", "莱恩·诺克斯"]
    assert pc_cast.mentioned_characters("约翰走进来", people, ctx=planner_ctx) == []
    assert pc_cast.mentioned_characters("约翰·华生走进来", people, ctx=planner_ctx) == ["约翰·华生"]
    assert pc_cast.mentioned_characters("赫尔曼走进来", people, ctx=planner_ctx) == ["赫尔曼"]          # longest match: not 赫尔男爵's 赫尔
    assert pc_cast.mentioned_characters("赫尔男爵和莱恩·诺克斯说话", people, ctx=planner_ctx) == ["赫尔男爵", "莱恩·诺克斯"]
    assert pc_cast.mentioned_characters("莱恩·格雷来了", people, ctx=planner_ctx) == ["莱恩·格雷"]     # 莱恩 alone is shared, the full name is not


def test_clip_cast_keeps_people_the_picture_names_by_a_short_form():
    import build_clip_plan_thin
    shot = {"characters": ["莱恩·格雷", "琥珀·高德", "薇奥拉公主"], "visual_prompt": "薇奥拉环着莱恩的脖子，琥珀猫卧在窗台上",
            "motion_prompt": "", "end_state": "", "turns": []}
    clip = {"shots": [shot]}
    assert build_clip_plan_thin.clip_cast(clip) == ["莱恩·格雷", "琥珀·高德", "薇奥拉公主"]
    assert clip.get("background_only", []) == []


def test_splice_keeps_untouched_clips_and_their_lane_prompts():
    old = {"policy": "p", "clips": [
        {"clip_id": "clip_01", "cast": ["a·b"], "prompt": "old1", "prompt_h3": "en1"},
        {"clip_id": "clip_02", "cast": ["a·b"], "prompt": "old2", "prompt_h3": "en2"}]}
    new = {"policy": "p", "clips": [
        {"clip_id": "clip_01", "cast": ["a·b"], "prompt": "new1-template"},
        {"clip_id": "clip_02", "cast": ["a·b", "c·d"], "prompt": "new2", "prompt_h3": "stale"}]}
    merged, changed = complete_cast_thin.splice_plans(old, new)
    assert changed == ["clip_02"]
    assert merged["clips"][0] == old["clips"][0]
    assert merged["clips"][1] == {"clip_id": "clip_02", "cast": ["a·b", "c·d"], "prompt": "new2"}
    assert complete_cast_thin.splice_plans(old, {"clips": [{"clip_id": "clip_01"}]}) == (None, [])


def test_aliases_from_the_novel_count_too(monkeypatch):
    planner_ctx = PlannerContext.from_env()
    monkeypatch.setitem(planner_ctx.aliases, "作家小姐", "艾琳娜")
    assert pc_cast.mentioned_characters("作家小姐有些不满", EVERYONE, ctx=planner_ctx) == ["艾琳娜"]


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
