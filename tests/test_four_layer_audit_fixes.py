"""The four-layer audit's confirmed defects, each closed by its own regression.

The audit (docs/meiman-four-layer-audit-20260925.md) reproduced these from saved repair output,
in memory, without a model: a repair model with nowhere legal to put an uncrewed suit; a
write-back that doubled a snap and reordered its consequences; one shot told to show a face,
a profile and a back at once; a solo shot inheriting the reflection of an armour off frame.
"""
from novel_manga.repair.contracts import schema_for
from novel_manga.repair.execution import apply_stage
from novel_manga.story.actions import action_text, anchored_event
from novel_manga.story.compilation import ClipCompiler, CompilerOptions
from novel_manga.story.scene import resolve_scene


# ---- audit #1: the repair schema offers scene_objects/props/wears/light, and the write-back keeps them

def test_the_repair_schema_offers_the_fields_the_prompt_names():
    schema = schema_for(["席勒", "托尼·斯塔克"], [1])
    stage = schema["properties"]["stages"]["items"]["properties"]
    for field in ("scene_objects", "props", "wears", "light"):
        assert field in stage, field
    # and they are optional: a repair that says nothing about them still validates
    assert set(schema["properties"]["stages"]["items"]["required"]) == {
        "origin_index", "in_frame", "actions", "extras", "event"}


def test_apply_stage_writes_back_the_new_fields_and_keeps_absent_ones():
    shot = {"origin_index": 1, "characters": ["席勒", "托尼·斯塔克"], "in_frame": ["席勒", "托尼·斯塔克"],
            "actions": [], "extras": [], "motion_prompt": "旧事件", "light": "台灯",
            "props": ["旧道具"], "turns": []}
    fix = {"origin_index": 1, "in_frame": ["席勒", "托尼·斯塔克"], "actions": [], "extras": [],
           "event": "托尼·斯塔克打响指，一套无人空机甲飞入屋内，席勒指着它说话。",
           "scene_objects": ["无人空机甲"], "wears": {"托尼·斯塔克": "马克2号机甲"}, "light": "台灯，夜晚"}
    apply_stage(shot, fix, ["席勒", "托尼·斯塔克"])
    assert shot["scene_objects"] == ["无人空机甲"]
    assert shot["wears"] == {"托尼·斯塔克": "马克2号机甲"}
    assert shot["light"] == "台灯，夜晚"
    assert shot["props"] == ["旧道具"]                    # absent from the fix: kept as it was


# ---- audit #2: a correct single event is not doubled or reordered on write-back

def test_a_target_inside_the_verb_is_not_appended_again():
    assert action_text([{"actor": "席勒", "action": "指着空机甲说话", "target": "空机甲"}]) == "席勒指着空机甲说话"


def test_a_carried_action_is_not_stapled_in_front_of_the_event():
    actions = [{"actor": "托尼·斯塔克", "action": "打响指", "target": ""},
               {"actor": "空机甲", "action": "飞入屋内并停稳", "target": ""},
               {"actor": "席勒", "action": "指着空机甲说话", "target": "空机甲"}]
    event = "托尼·斯塔克打响指，一套无人空机甲飞入屋内，席勒指着它说话。"
    assert anchored_event(actions, event) == event        # nothing doubled, nothing reordered


def test_a_truly_missing_attribution_is_still_spoken_first():
    assert anchored_event([{"actor": "莱恩", "action": "推门", "target": ""}], "门开了。") == "莱恩推门。门开了。"


# ---- audit #3: one shot is not told face, profile and back at once

def _options():
    from novel_manga.application.profiles import frame_spec
    return CompilerOptions(15, 9, 3, "execution", 8, frame=frame_spec({"frame": "16:9"}))


def test_a_listener_in_the_cast_is_not_also_named_as_facing():
    clip = {"clip_id": "clip_01", "kind": "video", "request_seconds": 10, "references": [], "lines": [],
            "shots": [{"index": 1, "origin_index": 1, "location": "诊所", "segment_id": "s1",
                       "shot_scale": "中景", "characters": ["托尼·斯塔克", "席勒"], "listeners": ["席勒"],
                       "visual_prompt": "诊室内", "motion_prompt": "托尼说话", "end_state": "说完",
                       "turns": [{"speaker_name": "托尼·斯塔克", "delivery_mode": "visible_dialogue", "text": "……"}],
                       "actions": [], "extras": [], "camera": "", "light": "", "sfx": "", "avoid": ""}]}
    from novel_manga.models.bible import Character, StoryBible
    bible = StoryBible(novel_title="t", genre="g", visual_style="3d", palette="c", style_fingerprint="f",
                       characters=[Character(name="托尼·斯塔克", role="主角", appearance="a", wardrobe="w"),
                                   Character(name="席勒", role="男主角", appearance="b", wardrobe="x")],
                       locations=["诊所：房间"])
    prompt = ClipCompiler(_options()).compile_prompt(clip, bible, ["托尼·斯塔克", "席勒"], [], "诊所")
    assert "只有托尼·斯塔克、席勒正脸入镜" not in prompt     # the contradictory line is gone
    assert "入镜人物：托尼·斯塔克" in prompt                # the facing list names who faces
    assert "听者席勒的站位、朝向与可见范围按本阶段画面描述" in prompt
    assert "席勒只露背影或在画外" not in prompt


def test_the_clip_roster_is_not_a_per_frame_headcount():
    """可出现名单，不是每帧人数：单拍一人的阶段不再被告知两人始终在场。"""
    clip = {"clip_id": "clip_01", "kind": "video", "request_seconds": 10, "references": [], "lines": [],
            "shots": [{"index": 1, "origin_index": 1, "location": "诊所", "segment_id": "s1",
                       "shot_scale": "中景", "characters": ["托尼·斯塔克"], "listeners": [],
                       "visual_prompt": "诊室内", "motion_prompt": "托尼独自站着", "end_state": "仍是独自",
                       "turns": [], "actions": [], "extras": [], "camera": "", "light": "", "sfx": "", "avoid": ""}]}
    from novel_manga.models.bible import Character, StoryBible
    bible = StoryBible(novel_title="t", genre="g", visual_style="3d", palette="c", style_fingerprint="f",
                       characters=[Character(name="托尼·斯塔克", role="主角", appearance="a", wardrobe="w"),
                                   Character(name="席勒", role="男主角", appearance="b", wardrobe="x")],
                       locations=["诊所：房间"])
    prompt = ClipCompiler(_options()).compile_prompt(clip, bible, ["托尼·斯塔克", "席勒"], [], "诊所")
    assert "画面中始终只有" not in prompt
    assert "本片段可出现的具名人物" in prompt


# ---- audit #4: a solo shot does not inherit an off-frame armour's reflection

def _script_for_light(shots):
    return {"shots": shots}


def test_same_as_light_drops_the_off_frame_reflection():
    script = _script_for_light([
        {"index": 1, "origin_index": 1, "location": "诊所", "characters": ["托尼·斯塔克", "席勒"],
         "visual_prompt": "", "motion_prompt": "托尼穿甲站着", "end_state": "", "turns": [], "actions": [],
         "camera": "平视", "light": "台灯暖光为主光，月光从窗外斜入；机甲装甲的金属反光为次光"},
        {"index": 2, "origin_index": 2, "location": "诊所", "characters": ["席勒"],
         "visual_prompt": "", "motion_prompt": "席勒独自坐着看信", "end_state": "", "turns": [], "actions": [],
         "camera": "同上", "light": "同上"},
    ])
    from novel_manga.story.scene import SceneContext
    resolved = resolve_scene(script, SceneContext(aliases={}, types={}, speaker_facts={}, identity={}, segments=[]))
    solo = resolved.shots[1]
    assert "反光" not in solo["light"]                      # the armour is off frame here
    assert "台灯" in solo["light"]                          # the environment light stays


def test_same_as_light_keeps_the_reflection_when_the_wearer_stays():
    script = _script_for_light([
        {"index": 1, "origin_index": 1, "location": "诊所", "characters": ["托尼·斯塔克", "席勒"],
         "visual_prompt": "", "motion_prompt": "托尼穿甲站着", "end_state": "", "turns": [], "actions": [],
         "camera": "平视", "light": "台灯暖光为主光；装甲金属反光为次光"},
        {"index": 2, "origin_index": 2, "location": "诊所", "characters": ["托尼·斯塔克"],
         "visual_prompt": "", "motion_prompt": "托尼转身", "end_state": "", "turns": [], "actions": [],
         "camera": "同上", "light": "同上", "wears": {"托尼·斯塔克": "马克2号机甲"}},
    ])
    from novel_manga.story.scene import SceneContext
    resolved = resolve_scene(script, SceneContext(aliases={}, types={}, speaker_facts={}, identity={}, segments=[]))
    assert "反光" in resolved.shots[1]["light"]             # the armour is still on camera
