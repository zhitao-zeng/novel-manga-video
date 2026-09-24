"""Catalogued props get asset bindings; uncatalogued scene objects stay objects, not extras."""
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


def test_stage_schema_enumerates_catalogued_props_but_allows_uncatalogued_objects():
    with_props = build_schema(["莱恩"], ["事务所"], ["s1"], ctx=_ctx(), prop_names=["青铜短剑"])
    stage_props = with_props["properties"]["clips"]["items"]["properties"]["stages"]["items"]["properties"]
    assert stage_props["props"]["items"]["enum"] == ["青铜短剑"]
    assert stage_props["props"]["maxItems"] == 2
    assert "props" not in with_props["properties"]["clips"]["items"]["properties"]["stages"]["items"]["required"]

    without = build_schema(["莱恩"], ["事务所"], ["s1"], ctx=_ctx())
    stage_without = without["properties"]["clips"]["items"]["properties"]["stages"]["items"]["properties"]
    assert "props" not in stage_without and "wears" not in stage_without
    assert stage_without["scene_objects"]["items"] == {"type": "string", "maxLength": 80}


def test_compact_bible_lists_props_only_when_present():
    shown = compact_bible(_bible(True), {"事务所": "事务所：临街小屋"})
    assert shown["props"] == [{"name": "青铜短剑", "appearance": "泛青", "category": "武器",
                               "aliases": [], "owner": "", "wearable": False}]
    assert "props" not in compact_bible(_bible(False), {"事务所": "事务所：临街小屋"})


def test_validation_keeps_catalogued_props_and_describes_uncatalogued_objects():
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
    assert stage.get("props") == ["青铜短剑"]
    assert stage.get("scene_objects") == ["幻激光枪"]
    assert any("幻激光枪" in w for w in result.warnings)

    raw["clips"][0]["stages"][0]["props"] = []
    result2 = validate_and_normalize(raw, [{"segment_id": "s1", "text": chapter}], _bible(True),
                                     {"事务所": "事务所：临街小屋"}, chapter, ctx=_ctx())
    assert "props" not in result2.shots[0]              # 空数组不落盘


def test_catalogued_wearable_cannot_be_an_unnamed_actor():
    from novel_manga.planning.validation import validate_and_normalize
    from novel_manga.planning.issues import PlanningCode
    bible = _bible(True)
    source = '莱恩拿着青铜短剑走进事务所。'
    stage = {'segment_id': 's1', 'source_quote': source, 'start_state': '莱恩拿着剑',
             'event': '莱恩走进来', 'end_state': '莱恩站在门口', 'camera': '平视', 'light': '日光',
             'sfx': '', 'shot_scale': '中景', 'in_frame': ['莱恩'], 'extras': ['青铜短剑'],
             'props': ['青铜短剑'], 'actions': [], 'turns': []}
    raw = {'clips': [{'clip_id': 'c', 'location': '事务所', 'characters': ['莱恩'], 'avoid': '',
                      'stages': [stage]}], 'skipped_segments': []}
    result = validate_and_normalize(raw, [{'segment_id': 's1', 'text': source}], bible,
                                    {'事务所': bible.locations[0]}, source, ctx=_ctx())
    assert PlanningCode.PROP_AS_EXTRA in {issue.code for issue in result.issues}


def test_mark2_wearer_cannot_silently_be_replaced_by_a_suit():
    from novel_manga.planning.validation import validate_and_normalize
    from novel_manga.planning.issues import PlanningCode
    bible = StoryBible(novel_title='美漫', genre='urban', visual_style='2d', palette='', style_fingerprint='f',
                       characters=[Character(name='托尼·斯塔克', appearance='中年男子', wardrobe='西装')],
                       locations=['诊室：办公桌'], props=[Prop(name='马克2机甲', category='穿戴装甲', wearable=True,
                                                           owner='托尼·斯塔克', first_chapter=12)])
    source = '斯塔克穿着马克2机甲飞进诊室，面罩打开。'
    stage = {'segment_id': 's1', 'source_quote': source, 'start_state': '斯塔克穿着马克2',
             'event': '托尼·斯塔克穿着马克2飞入', 'end_state': '托尼站在桌前',
             'camera': '平视', 'light': '台灯', 'sfx': '喷气声', 'shot_scale': '中景',
             'in_frame': ['托尼·斯塔克'], 'extras': ['马克2机甲'], 'props': ['马克2机甲'],
             'wears': {'托尼·斯塔克': '西装'}, 'actions': [], 'turns': []}
    raw = {'clips': [{'clip_id': 'c', 'location': '诊室', 'characters': ['托尼·斯塔克'], 'avoid': '',
                      'stages': [stage]}], 'skipped_segments': []}
    result = validate_and_normalize(raw, [{'segment_id': 's1', 'text': source}], bible,
                                    {'诊室': bible.locations[0]}, source, ctx=_ctx())
    assert {PlanningCode.PROP_AS_EXTRA, PlanningCode.INVALID_WEARABLE} <= {issue.code for issue in result.issues}


def test_uncatalogued_worn_object_survives_as_an_object_without_a_second_actor():
    from novel_manga.planning.validation import validate_and_normalize
    chapter = "莱恩穿着银色机甲飞入事务所，旁人看着他。"
    raw = {"clips": [{"clip_id": "clip_1", "location": "事务所", "characters": ["莱恩"], "avoid": "",
                      "stages": [{"segment_id": "s1", "source_quote": chapter,
                                  "start_state": "莱恩穿着银色机甲站在门口", "event": "莱恩穿着机甲走入",
                                  "end_state": "莱恩仍穿着机甲", "camera": "平视", "light": "室内", "sfx": "机甲落地声",
                                  "shot_scale": "中景", "in_frame": ["莱恩"], "extras": ["银色机甲"], "actions": [],
                                  "props": ["银色机甲"], "wears": {"莱恩": "银色机甲"},
                                  "turns": [{"speaker_name": "", "delivery_mode": "silent_action", "text": "走入",
                                             "emotion": "平静", "chat_target": ""}]}]}], "skipped_segments": []}
    bible = _bible(False)
    result = validate_and_normalize(raw, [{"segment_id": "s1", "text": chapter}], bible,
                                    {"事务所": bible.locations[0]}, chapter, ctx=_ctx())
    assert not result.errors
    assert result.shots[0]["scene_objects"] == ["银色机甲"]
    assert "props" not in result.shots[0]
    assert result.shots[0]["wears"] == {"莱恩": "银色机甲"}
    assert result.shots[0]["extras"] == []


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
