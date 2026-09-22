"""Props in the planning contract and the compact bible: present when the book has props,
absent - byte-identical - when it does not."""
from novel_manga.planning.contracts import build_schema
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.prompts import compact_bible
from novel_manga.models.bible import Character, Prop, StoryBible


def _ctx():
    return PlannerContext()


def _bible(with_props=True):
    return StoryBible(
        novel_title="t", genre="g", visual_style="s", palette="p", style_fingerprint="fp",
        characters=[Character(name="莱恩", appearance="瘦高", wardrobe="黑大衣")],
        locations=["事务所：临街小屋"],
        props=[Prop(name="青铜短剑", category="武器", appearance="泛青", first_chapter=1, quote="…")]
        if with_props else [],
    )


def test_stage_schema_has_props_enum_only_when_props_exist():
    with_props = build_schema(["莱恩"], ["事务所"], ["s1"], ctx=_ctx(), prop_names=["青铜短剑"])
    stage_props = with_props["properties"]["clips"]["items"]["properties"]["stages"]["items"]["properties"]
    assert stage_props["props"]["items"]["enum"] == ["青铜短剑"]
    assert stage_props["props"]["maxItems"] == 2
    assert "props" not in with_props["properties"]["clips"]["items"]["properties"]["stages"]["items"]["required"]

    without = build_schema(["莱恩"], ["事务所"], ["s1"], ctx=_ctx())
    stage_without = without["properties"]["clips"]["items"]["properties"]["stages"]["items"]["properties"]
    assert "props" not in stage_without           # 老书：schema 里没有这个键


def test_compact_bible_lists_props_only_when_present():
    shown = compact_bible(_bible(True), {"事务所": "事务所：临街小屋"})
    assert shown["props"] == [{"name": "青铜短剑", "appearance": "泛青", "category": "武器"}]
    assert "props" not in compact_bible(_bible(False), {"事务所": "事务所：临街小屋"})


def test_validation_keeps_catalogued_props_and_drops_hallucinations():
    from novel_manga.planning.validation import validate_and_normalize
    chapter = "他抽出那柄青铜短剑，走向门口。"
    raw = {"video_title": "t", "hook": "", "summary": "", "skipped_segments": [],
           "clips": [{"clip_id": "clip_1", "location": "事务所", "characters": ["莱恩"], "avoid": "",
                      "stages": [{"segment_id": "s1", "source_quote": "他抽出那柄青铜短剑",
                                  "start_state": "", "event": "拔剑", "end_state": "",
                                  "camera": "固定", "light": "室内", "sfx": "", "shot_scale": "近景",
                                  "in_frame": ["莱恩"], "extras": [], "actions": [],
                                  "props": ["青铜短剑", "幻激光枪"],
                                  "turns": [{"speaker_name": "", "delivery_mode": "narration",
                                             "text": "", "emotion": ""}]}]}]}
    result = validate_and_normalize(raw, [{"segment_id": "s1", "text": chapter}], _bible(True),
                                    {"事务所": "事务所：临街小屋"}, chapter, ctx=_ctx())
    assert not result.errors, [str(e) for e in result.errors]
    stage = result.shots[0]
    assert stage.get("props") == ["青铜短剑"]            # 幻觉名字被丢弃
    assert any("幻激光枪" in w for w in result.warnings)

    raw["clips"][0]["stages"][0]["props"] = []
    result2 = validate_and_normalize(raw, [{"segment_id": "s1", "text": chapter}], _bible(True),
                                     {"事务所": "事务所：临街小屋"}, chapter, ctx=_ctx())
    assert "props" not in result2.shots[0]              # 空数组不落盘


def test_bind_schema_optional_props_and_merge_carries_them():
    from novel_manga.planning.binding import bind_schema, merge
    authored = {"shots": [{"镜号": "1", "台词 / 声音": "", "场景": "事务所", "画面内容 / 动作": "拔剑",
                           "景别": "近景", "摄影角度": "平视", "机位 / 运镜 / 连续性": "",
                           "叙事目的": "", "预算秒": 5}]}
    schema = bind_schema(authored, ["莱恩"], ["事务所"], ["s1"], ctx=_ctx(), prop_names=["青铜短剑"])
    bound_props = schema["properties"]["bindings"]["items"]["properties"]
    assert bound_props["props"]["items"]["enum"] == ["青铜短剑"]
    assert "props" not in schema["properties"]["bindings"]["items"]["required"]

    answer = {"video_title": "t", "hook": "", "summary": "",
              "bindings": [{"镜号": "1", "segment_id": "s1", "source_quote": "q", "start_state": "",
                            "end_state": "", "light": "", "in_frame": ["莱恩"], "extras": [],
                            "actions": [], "props": ["青铜短剑"]}],
              "skipped_segments": [], "speaker_names": []}
    merged = merge(authored, answer, character_names=["莱恩"])
    assert merged["clips"][0]["stages"][0]["props"] == ["青铜短剑"]
