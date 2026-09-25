"""The storyboard contract: a stage's in_frame is its cast, its actions name who does what to whom and lead the
event line, the ledger decides the chapter's candidates when it has read the chapter, and the packer's second
reference view stays off unless asked for."""
from __future__ import annotations
from novel_manga.story.compilation import ClipCompiler
from novel_manga.application.packing.context import compiler_options
import novel_manga.application.packing.assets as packing_assets
import novel_manga.application.packing.context as packing_context
import novel_manga.application.packing.service as packing_service

import json
import os
import sys
from pathlib import Path

import novel_manga.planning.outputs as pc_outputs
import novel_manga.planning.validation as pc_validation
import novel_manga.application.planning.context as planner_context
from novel_manga.planning.context import PlannerContext  # noqa: E402
from novel_manga.models.bible import Character, StoryBible

TEXT = "薇奥拉站起身绕过桌子，伸手双臂环住莱恩的脖子，微微踮起脚尖吻住了他。但莱恩向后仰头想要避开：“塞西娅还在楼上。”"


def bible() -> StoryBible:
    return StoryBible(novel_title="雾月", genre="gaslamp", visual_style="2d", palette="p", style_fingerprint="f",
                      characters=[Character(name="莱恩·格雷", role="主角", appearance="黑发青年", wardrobe="大衣"),
                                  Character(name="薇奥拉公主", role="公主", appearance="红发", wardrobe="长裙"),
                                  Character(name="塞西娅", role="", appearance="棕发", wardrobe="衬衫")],
                      locations=["夜莺广场：河边的小广场"])


def test_in_frame_and_actions_lead_the_event_line():
    planner_ctx = PlannerContext.from_env()
    raw = {"clips": [{"clip_id": "clip_01", "location": "夜莺广场", "characters": ["莱恩·格雷", "薇奥拉公主", "塞西娅"], "avoid": "", "stages": [
        {"segment_id": "seg_1", "source_quote": "伸手双臂环住莱恩的脖子，微微踮起脚尖吻住了他", "start_state": "薇奥拉站起身", "event": "薇奥拉吻莱恩，莱恩避开",
         "end_state": "莱恩仰头", "camera": "桌边", "light": "灯", "sfx": "无", "shot_scale": "中近景",
         "in_frame": ["薇奥拉公主", "莱恩·格雷"],
         "actions": [{"actor": "薇奥拉公主", "action": "环住脖子踮脚吻住", "target": "莱恩·格雷"}, {"actor": "莱恩·格雷", "action": "向后仰头避开", "target": ""}],
         "turns": [{"speaker_name": "莱恩·格雷", "delivery_mode": "visible_dialogue", "text": "塞西娅还在楼上。", "emotion": "克制", "chat_target": ""}]}]}],
           "skipped_segments": []}
    segments = [{"segment_id": "seg_1", "text": TEXT}]
    b = bible()
    validation = pc_validation.validate_and_normalize(raw, segments, b, {"夜莺广场": b.locations[0]}, TEXT, ctx=planner_ctx)
    errors, warnings, shots = validation.errors, validation.warnings, validation.shots
    assert not [e for e in errors if "seg_1" not in e], errors
    shot = shots[0]
    assert shot["characters"] == ["薇奥拉公主", "莱恩·格雷"]          # nothing here names 塞西娅
    # Once a stage's own text names her, she joins - even though "塞西娅还在楼上" says she is not in
    # this frame.  That is the ghost the in_frame guard was closed to stop, and on 2026-09-22 the user
    # reopened it against the other side of the ledger: 244 of 在美漫当心灵导师的日子's 1,413 shots name
    # someone their cast omits, and every one of those reached H3 without a card, as "Stark" or
    # "a seated figure", for it to invent a face for.  A wrong extra face costs one shot; a missing
    # card costs the character their identity in every shot they appear in.  The add is capped at six
    # and every one of them is named in a warning, which is how a bad one gets found.
    raw["clips"][0]["stages"][0]["event"] = "薇奥拉吻莱恩，莱恩避开说塞西娅还在楼上"
    validation = pc_validation.validate_and_normalize(raw, segments, b, {"夜莺广场": b.locations[0]}, TEXT, ctx=planner_ctx)
    _, warnings2, shots2 = validation.errors, validation.warnings, validation.shots
    assert any("塞西娅" in w and "补上" in w for w in warnings2)
    # The two layers then compose: the scan puts her in the shot, and framing - one visible speaker -
    # turns her into a listener rather than a second face competing for the frame.  She keeps her
    # reference card either way, which is the whole point: her back is her back, not a stranger's.
    assert shots2[0]["characters"] == ["薇奥拉公主", "莱恩·格雷"]
    assert shots2[0]["listeners"] == ["塞西娅"]
    assert shot["motion_prompt"].startswith("薇奥拉公主环住脖子踮脚吻住莱恩·格雷；莱恩·格雷向后仰头避开。")
    assert shot["actions"] == [{"actor": "薇奥拉公主", "action": "环住脖子踮脚吻住", "target": "莱恩·格雷"}, {"actor": "莱恩·格雷", "action": "向后仰头避开", "target": ""}]


def test_one_visible_speaker_keeps_only_the_speaker_in_frame():
    """Shot / reverse shot: with 莱恩 speaking and no action involving 塞西娅, she becomes a listener (back to camera or
    off frame) and the prompt says so; when the stage's action reaches her, both stay in frame."""
    planner_ctx = PlannerContext.from_env()
    pass
    stage = {"segment_id": "seg_1", "source_quote": "伸手双臂环住莱恩的脖子，微微踮起脚尖吻住了他", "start_state": "两人对坐", "event": "莱恩说话",
             "end_state": "塞西娅沉默", "camera": "桌边", "light": "灯", "sfx": "无", "shot_scale": "中近景", "in_frame": ["莱恩·格雷", "塞西娅"],
             "actions": [], "extras": [], "turns": [{"speaker_name": "莱恩·格雷", "delivery_mode": "visible_dialogue", "text": "塞西娅还在楼上。", "emotion": "", "chat_target": ""}]}
    raw = {"clips": [{"clip_id": "clip_01", "location": "夜莺广场", "characters": ["莱恩·格雷", "塞西娅"], "avoid": "", "stages": [stage]}], "skipped_segments": []}
    b = bible()
    validation = pc_validation.validate_and_normalize(raw, [{"segment_id": "seg_1", "text": TEXT}], b, {"夜莺广场": b.locations[0]}, TEXT, ctx=planner_ctx)
    _, warnings, shots = validation.errors, validation.warnings, validation.shots
    assert shots[0]["characters"] == ["莱恩·格雷"] and shots[0]["listeners"] == ["塞西娅"] and any("转为听者" in w for w in warnings)
    plan = pc_outputs.to_episode_plan(raw, shots, {"夜莺广场": b.locations[0]}, TEXT, "第一章", ctx=planner_ctx)
    assert plan.shots[0].listeners == ["塞西娅"]
    clip = {"request_seconds": 15, "shots": [{**shots[0], "visual_prompt": "莱恩说话", "motion_prompt": "莱恩说话", "end_state": "塞西娅沉默"}]}
    assert "入镜人物：莱恩·格雷；听者塞西娅的站位、朝向与可见范围按本阶段画面描述" in ClipCompiler(compiler_options()).compile_prompt(clip, b, ['莱恩·格雷'], [], '夜莺广场：河边的小广场')
    stage["actions"] = [{"actor": "莱恩·格雷", "action": "握住手腕", "target": "塞西娅"}]
    validation = pc_validation.validate_and_normalize(raw, [{"segment_id": "seg_1", "text": TEXT}], b, {"夜莺广场": b.locations[0]}, TEXT, ctx=planner_ctx)
    _, _, shots2 = validation.errors, validation.warnings, validation.shots
    assert shots2[0]["characters"] == ["莱恩·格雷", "塞西娅"] and shots2[0]["listeners"] == []


def test_uncarded_extras_are_kept_by_description_and_reach_the_prompt():
    """An unnamed neighbour in the passage is an extra drawn from her description; a named character written as an
    extra is dropped; the packer's headcount line and the stage line carry the extras."""
    planner_ctx = PlannerContext.from_env()
    pass
    raw = {"clips": [{"clip_id": "clip_01", "location": "夜莺广场", "characters": ["莱恩·格雷"], "avoid": "", "stages": [
        {"segment_id": "seg_1", "source_quote": "伸手双臂环住莱恩的脖子，微微踮起脚尖吻住了他", "start_state": "莱恩站着", "event": "老妇人递信",
         "end_state": "莱恩接信", "camera": "门口", "light": "灯", "sfx": "无", "shot_scale": "中近景", "in_frame": ["莱恩·格雷"],
         "actions": [], "extras": ["戴眼镜的灰发老妇人", "薇奥拉公主", "  "],
         "turns": [{"speaker_name": "", "delivery_mode": "silent_action", "text": "递信", "emotion": "", "chat_target": ""}]}]}], "skipped_segments": []}
    b = bible()
    validation = pc_validation.validate_and_normalize(raw, [{"segment_id": "seg_1", "text": TEXT}], b, {"夜莺广场": b.locations[0]}, TEXT, ctx=planner_ctx)
    _, _, shots = validation.errors, validation.warnings, validation.shots
    assert shots[0]["extras"] == ["戴眼镜的灰发老妇人"]
    plan = pc_outputs.to_episode_plan(raw, shots, {"夜莺广场": b.locations[0]}, TEXT, "第一章", ctx=planner_ctx)
    assert plan.shots[0].extras == ["戴眼镜的灰发老妇人"]
    clip = {"request_seconds": 15, "shots": [{**shots[0], "visual_prompt": "莱恩站在门口", "motion_prompt": "老妇人递信", "end_state": "莱恩接信"}]}
    prompt = ClipCompiler(compiler_options()).compile_prompt(clip, b, ['莱恩·格雷'], [], '夜莺广场：河边的小广场')
    assert "另加1位无参考图的配角（按描述画，不得画成具名人物的样子）：戴眼镜的灰发老妇人" in prompt
    assert "本阶段无参考图的配角：戴眼镜的灰发老妇人（按描述画）" in prompt


def test_ledger_cast_and_snapshot_come_from_the_ledger_files(tmp_path):
    novel = tmp_path / "wuyue"
    (novel / "entity" / "mentions").mkdir(parents=True)
    (novel / "entity" / "entities.json").write_text(json.dumps([
        {"id": "e001", "canonical": "莱恩·格雷", "status": "active", "merged_into": None},
        {"id": "e002", "canonical": "薇奥拉公主", "status": "active", "merged_into": None},
        {"id": "e003", "canonical": "塞西娅", "status": "active", "merged_into": None},
        {"id": "e004", "canonical": "作家小姐", "status": "merged", "merged_into": "e002"}]), encoding="utf-8")
    (novel / "entity" / "mentions" / "ch_0761.json").write_text(json.dumps([
        {"form": "莱恩", "entity": "e001", "kind": "proper", "presence": "on_stage"},
        {"form": "薇奥拉", "entity": "e002", "kind": "proper", "presence": "on_stage"},
        {"form": "作家小姐", "entity": "e004", "kind": "contextual", "presence": "on_stage"},
        {"form": "塞西娅", "entity": "e003", "kind": "proper", "presence": "mentioned"}]), encoding="utf-8")
    (novel / "entity" / "claims.json").write_text(json.dumps([
        {"chapter": 900, "type": "occupies_body", "subject": "e003", "object": "e002", "hidden_from_reader": True}]), encoding="utf-8")
    cast = planner_context.ledger_cast(novel, 761)
    assert cast == {"莱恩·格雷": "on_stage", "薇奥拉公主": "on_stage", "塞西娅": "mentioned"}
    assert planner_context.ledger_cast(novel, 762) == {}                                   # not read: the old candidates apply
    snap = planner_context.ledger_snapshot_for(novel, 761, [{"segment_id": "seg_4", "text": TEXT}, {"segment_id": "seg_5", "text": "作家小姐笑了。"}], cast,
                                  ["莱恩·格雷", "薇奥拉公主", "塞西娅"])
    assert snap["segments"]["seg_4"]["named_here"] == ["莱恩·格雷", "薇奥拉公主", "塞西娅"]
    assert snap["segments"]["seg_5"]["named_here"] == ["薇奥拉公主"]        # a merged record's form names the survivor
    assert snap["must_not_reveal"] == ["塞西娅 occupies_body 薇奥拉公主"]


def test_second_view_is_off_unless_asked(monkeypatch, tmp_path):
    pass
    b = bible()
    cards = tmp_path / "series_assets" / "characters" / "character_001"
    cards.mkdir(parents=True)
    (cards / "expressions.jpeg").write_bytes(b"x")
    monkeypatch.setattr(packing_context, "TWO_VIEWS", "off")
    refs, _, _ = packing_assets.build_references(["莱恩·格雷"], "夜莺广场", b, {"夜莺广场": b.locations[0]}, novel_dir=tmp_path)
    assert [r["path"] for r in refs if r["role"] == "character"] == ["series_assets/characters/character_001/turnaround.jpeg"]
    monkeypatch.setattr(packing_context, "TWO_VIEWS", "all")
    refs, _, _ = packing_assets.build_references(["莱恩·格雷"], "夜莺广场", b, {"夜莺广场": b.locations[0]}, novel_dir=tmp_path)
    assert [r["path"] for r in refs if r["role"] == "character"][-1].endswith("expressions.jpeg")


def test_references_follow_the_body_the_ledger_names(monkeypatch, tmp_path):
    """A character the ledger puts in someone else's body references that body's card and says so in the binding."""
    pass
    from novel_manga.models.bible import Character, StoryBible
    bible = StoryBible(novel_title="雾月", genre="gaslamp", visual_style="2d", palette="p", style_fingerprint="f", characters=[
        Character(name="艾琳娜", role="配角", appearance="银发", wardrobe="紫裙", hair="银色长发"),
        Character(name="薇奥拉公主", role="配角", appearance="金发", wardrobe="白裙", hair="金色长发"),
    ], locations=["书房：安静的书房"])
    monkeypatch.setattr(packing_assets, 'bodies_for', lambda novel_dir, chapter: {"艾琳娜": ("薇奥拉公主", "character_002")})
    references, bindings, _ = packing_assets.build_references(["艾琳娜"], "书房", bible, {"书房": "书房：安静的书房"}, novel_dir=tmp_path, chapter=546)
    assert references[0]["asset_id"] == "character_002"
    assert "在薇奥拉公主的身体里" in bindings[0]
    assert "金" in bindings[0] or "薇奥拉公主的样子" in bindings[0]


def test_every_stage_with_two_or_more_people_says_where_they_stand():
    pass
    b = bible()
    shot = {"segment_id": "seg_1", "shot_scale": "中近景", "visual_prompt": "两人在桌边", "motion_prompt": "薇奥拉公主吻住莱恩·格雷", "end_state": "莱恩避开",
            "camera": "桌边", "light": "灯", "sfx": "无", "characters": ["薇奥拉公主", "莱恩·格雷", "塞西娅"],
            "actions": [{"actor": "薇奥拉公主", "action": "吻住", "target": "莱恩·格雷"}], "turns": [], "listeners": [], "extras": []}
    prompt = ClipCompiler(compiler_options()).compile_prompt({'request_seconds': 10, 'shots': [shot]}, b, ['薇奥拉公主', '莱恩·格雷', '塞西娅'], [], '夜莺广场：河边的小广场')
    assert "构图：薇奥拉公主在画面左侧前景，莱恩·格雷在右侧前景，两人侧面相对、各占一侧；塞西娅只在后景侧身或背对镜头，不开口、不做主要动作。" in prompt
    solo = {**shot, "characters": ["莱恩·格雷"], "actions": []}
    assert "构图：莱恩" not in ClipCompiler(compiler_options()).compile_prompt({'request_seconds': 10, 'shots': [solo]}, b, ['莱恩·格雷'], [], '夜莺广场：河边的小广场')
    alone_among = {**shot, "characters": ["莱恩·格雷", "塞西娅"], "actions": [{"actor": "莱恩·格雷", "action": "推开门", "target": ""}]}
    assert "构图：莱恩·格雷在前景居中；塞西娅只在后景侧身或背对镜头" in ClipCompiler(compiler_options()).compile_prompt({'request_seconds': 10, 'shots': [alone_among]}, b, ['莱恩·格雷', '塞西娅'], [], '夜莺广场：河边的小广场')
    reached = {**shot, "actions": [{"actor": "薇奥拉公主", "action": "吻住", "target": "莱恩·格雷"}, {"actor": "莱恩·格雷", "action": "推开", "target": "塞西娅"}]}
    later = ClipCompiler(compiler_options()).compile_prompt({'request_seconds': 10, 'shots': [reached]}, b, ['薇奥拉公主', '莱恩·格雷', '塞西娅'], [], '夜莺广场：河边的小广场')
    assert "构图：薇奥拉公主在画面左侧前景，莱恩·格雷在右侧前景，两人侧面相对、各占一侧。" in later and "塞西娅只在后景" not in later
