import copy

import pytest

from novel_manga.application.agents.episode import contracts
from novel_manga.application.profiles import h3_prompt_outdated, h3_stamp
from novel_manga.story.voice_delivery import visual_request


from support.episode_agent import CATALOG, SOURCES, episode, prompt, shot


def adopt(draft, profile=None, catalog=None):
    return contracts.adopt(draft, catalog=CATALOG if catalog is None else catalog,
                           source_segments=SOURCES, profile=profile or {})


def test_adopts_authored_words_actions_reference_order_and_independent_copies(monkeypatch):
    turns = [{"speaker_name": "甲", "text": "你来啦，", "delivery_mode": "visible_dialogue"},
             {"speaker_name": "甲", "text": "坐吧。", "delivery_mode": "visible_dialogue"}]
    draft = episode(turns=turns)
    draft["chapter_script"]["shots"][0]["actions"] = [{"actor": "甲", "action": "抬眼", "target": ""}]
    saved = copy.deepcopy((draft, CATALOG, SOURCES))
    monkeypatch.setattr("novel_manga.story.compilation.ClipCompiler.compile_prompt",
                        lambda *a, **k: pytest.fail("adoption must not compile creative directions"))
    script, plan = adopt(draft)
    current = plan["clips"][0]
    assert (draft, CATALOG, SOURCES) == saved
    assert [r["id"] for r in current["references"]] == ["b", "room", "a", "a_voice"]
    assert [r["tag"] for r in current["references"]] == ["@图片1", "@图片2", "@图片3", "@音频1"]
    assert [r["text"] for r in current["dialogue_bindings"]] == ["你来啦，", "坐吧。"]
    assert current["spoken_text"] == "你来啦，坐吧。"
    assert current["action_bindings"][0] == {"stage": 1, "source_stage": 1, "actions": draft["chapter_script"]["shots"][0]["actions"]}
    assert current["prompt"] == "" and current["prompt_h3"] == draft["clips"][0]["prompt_h3"]
    assert current["prompt_h3_of"] == h3_stamp(current) and not h3_prompt_outdated(current, strict=True)
    assert script["shots"][0]["source_refs"] == [{"segment_id": r["segment_id"], "source_quote": r["text"]} for r in SOURCES]
    assert script["shots"][0]["source_quote"] == "\n".join(r["text"] for r in SOURCES)
    assert script["shots"][0]["visual_prompt"] == draft["chapter_script"]["shots"][0]["start_state"]
    current["references"][0]["name"] = "changed"
    script["shots"][0]["actions"][0]["target"] = "changed"
    assert (draft, CATALOG, SOURCES) == saved


def test_accepts_animals_props_self_actions_empty_targets_and_open_offscreen_speakers():
    turns = [{"speaker_name": "临时访客", "text": "我在这里。", "delivery_mode": "offscreen_dialogue"}]
    draft = episode(turns=turns, voice=False)
    draft["chapter_script"]["shots"][0].update(
        extras=["灰色野山羊"], scene_objects=["杯子"],
        actions=[{"actor": "灰色野山羊", "action": "碰触", "target": "杯子"},
                 {"actor": "甲", "action": "整理自己衣领", "target": "甲"},
                 {"actor": "甲", "action": "站起", "target": ""}])
    script, plan = adopt(draft)
    assert script["shots"][0]["actions"] == draft["chapter_script"]["shots"][0]["actions"]
    assert plan["clips"][0]["dialogue_bindings"][0]["speaker_name"] == "临时访客"
    assert script["shots"][1]["wears"] == {} and script["shots"][1]["actions"] == []


def test_worn_and_independent_prop_remain_physical_reference_metadata():
    catalog = [*CATALOG, {"id": "armor", "role": "prop", "name": "银甲", "path": "props/armor.jpeg"}]
    draft = episode(turns=[], silent=True, voice=False)
    for row, clip in zip(draft["chapter_script"]["shots"], draft["clips"]):
        row.update(props=["银甲"], independent_props=["银甲"], wears={"甲": "银甲", "乙": None})
        clip["reference_ids"].append("armor")
        clip["prompt_h3"] = clip["prompt_h3"].replace(
            "\n\nsummary:", "\n<Subject 4> is the physical object shown in <Picture 4>.\n\nsummary:")
    script, plan = adopt(draft, catalog=catalog)
    reference = plan["clips"][0]["references"][-1]
    assert reference["role"] == "prop" and reference["wearers"] == ["甲"] and reference["independent"] is True
    assert script["shots"][0]["wears"] == {"甲": "银甲", "乙": None}
    assert plan["clips"][0]["cast"] == ["甲", "乙"]
    assert "wearers" not in catalog[-1] and "independent" not in catalog[-1]


def test_multiple_authored_shots_have_explicit_cuts_and_source_stage_addresses():
    draft = episode()
    draft["chapter_script"]["shots"].insert(1, shot(2))
    for index, row in enumerate(draft["chapter_script"]["shots"], 1):
        row["index"] = index
    first = draft["chapter_script"]["shots"][:2]
    first[0]["duration_seconds"], first[1]["duration_seconds"] = 6, 9
    draft["clips"][0].update(shot_indexes=[1, 2], prompt_h3=prompt(first))
    for index, clip in enumerate(draft["clips"][1:], 3):
        clip["shot_indexes"] = [index]
    script, plan = adopt(draft)
    clip = plan["clips"][0]
    assert clip["shot_timing"] == [{"seconds": 6, "cut": ""}, {"seconds": 9, "cut": ""}]
    assert [(r["stage"], r["source_stage"]) for r in clip["dialogue_bindings"]] == [(1, 1), (2, 2)]
    assert [s["index"] for s in script["shots"]] == list(range(1, 10))
    assert plan["totals"]["requested_seconds"] == 120
    draft["chapter_script"]["shots"][1]["location"] = "下一场景"
    with pytest.raises(ValueError, match="one location"):
        adopt(draft)


@pytest.mark.parametrize("change, message", [
    (lambda d: d["clips"][0].update(reference_ids=["missing"]), "unknown reference"),
    (lambda d: d["chapter_script"]["shots"][0].update(segment_ids=["missing"]), "unknown source"),
    (lambda d: d["clips"][0].update(request_seconds=16), "3–15"),
    (lambda d: d["chapter_script"]["shots"][0].update(duration_seconds=14), "durations"),
    (lambda d: d["clips"][1].update(shot_indexes=[1]), "exactly once"),
    (lambda d: (d["clips"].pop(), d["chapter_script"]["shots"].pop()), "120–150"),
])
def test_rejects_only_concrete_address_and_timing_failures(change, message):
    draft = episode()
    change(draft)
    with pytest.raises(ValueError, match=message):
        adopt(draft)


@pytest.mark.parametrize("before, after, message", [
    ("shown in <Picture 3>", "shown in <Picture 2>", "Subject/Picture"),
    ("<Subject 2> (S1) says", "<Subject 1> (S1) says", "packed source owner"),
    ("你来啦。", "你来啦！", "exact authored words"),
    ("(S1) says", "(S2) says", "speaker IDs"),
    ("<Subject 2> (S1).", "<Subject 1> (S1).", "canonical speaker"),
    ("from <Audio 1>.", "from <Audio 2>.", "Audio tags"),
    ("[Shot 1]", "[Shot 2]", "stage"),
    ("Planned duration: 15", "Planned duration: 14", "planned duration"),
    ("A steady breeze.", "No wind.", "affirmative"),
])
def test_final_request_preserves_reference_dialogue_stage_and_affirmative_contract(before, after, message):
    draft = episode()
    draft["clips"][0]["prompt_h3"] = draft["clips"][0]["prompt_h3"].replace(before, after)
    with pytest.raises(ValueError, match=message):
        adopt(draft)


def test_catalog_paths_are_book_relative():
    catalog = copy.deepcopy(CATALOG)
    catalog[0]["path"] = "../other-book/a.jpeg"
    with pytest.raises(ValueError, match="book-relative"):
        adopt(episode(), catalog=catalog)


@pytest.mark.parametrize("notes", ["独立压缩。", ["保留来访原因。", "调整开场节奏。"], []])
def test_adaptation_notes_accepts_string_or_preserved_list_metadata(notes):
    draft = episode()
    draft["adaptation_notes"] = copy.deepcopy(notes)
    script, _ = adopt(draft)
    assert script["adaptation_notes"] == notes and draft["adaptation_notes"] == notes
    if isinstance(notes, list):
        script["adaptation_notes"].append("caller independent")
        assert draft["adaptation_notes"] == notes


def test_authored_sfx_array_and_single_extra_description_map_without_content_edits():
    draft = episode()
    row = draft["chapter_script"]["shots"][0]
    sounds = ["唱片怪音戛然而止", "手掌拍木机箱的闷响", "手掌拍木机箱的闷响"]
    description = "本镜仅呈现听到脑内声音后的身体反应。"
    row.update(sfx=sounds.copy(), extras=description)
    saved = copy.deepcopy(draft)
    script, plan = adopt(draft)
    assert script["shots"][0]["sfx"] == "; ".join(sounds)
    assert script["shots"][0]["authored_sfx"] == sounds
    assert plan["clips"][0]["shot_sound"] == ["; ".join(sounds)]
    assert script["shots"][0]["extras"] == [description]
    assert plan["clips"][0]["extras"] == [description]
    assert [{k: turn[k] for k in ("speaker_name", "text", "delivery_mode")}
            for turn in script["shots"][0]["turns"]] == saved["chapter_script"]["shots"][0]["turns"]
    assert plan["clips"][0]["prompt_h3"] == saved["clips"][0]["prompt_h3"]
    assert draft == saved


def test_postmix_preserves_thought_and_authored_camera_without_native_speech():
    turns = [{"speaker_name": "甲", "text": "我想明白了。", "delivery_mode": "offscreen_dialogue", "inner_monologue": True}]
    draft = episode(turns=turns, silent=True)
    script, plan = adopt(draft, {"inner_voice_delivery": "postmix"})
    clip = plan["clips"][0]
    assert clip["audio_delivery"] == "postmix"
    assert clip["inner_voice"]["text"] == "我想明白了。"
    assert clip["inner_voice"]["character_references"][0]["id"] == "a"
    assert clip["inner_voice"]["voice_references"][0]["id"] == "a_voice"
    assert clip["lines"][0]["inner_monologue"] is True and clip["spoken_text"] == "我想明白了。"
    assert all(r["role"] != "voice" for r in clip["references"])
    assert visual_request(clip)["dialogue_bindings"] == []
    assert script["shots"][0]["camera"] == draft["chapter_script"]["shots"][0]["camera"]
    assert clip["prompt_h3"] == draft["clips"][0]["prompt_h3"]
    assert clip["prompt_h3_of"] == h3_stamp(clip)


def test_mixed_postmix_requires_explicit_authored_clip_split_and_keeps_inputs():
    turns = [{"speaker_name": "甲", "text": "我想明白了。", "delivery_mode": "offscreen_dialogue", "inner_monologue": True},
             {"speaker_name": "甲", "text": "你来啦。", "delivery_mode": "visible_dialogue"}]
    draft = episode(turns=turns)
    saved = copy.deepcopy(draft)
    with pytest.raises(ValueError, match="separate pure inner_monologue"):
        adopt(draft, {"inner_voice_delivery": "postmix"})
    assert draft == saved


def test_native_mixed_context_preserves_explicit_thought_without_guessing_camera_or_retention():
    turns = [{"speaker_name": "甲", "text": "我想明白了。", "delivery_mode": "offscreen_dialogue", "inner_monologue": True},
             {"speaker_name": "乙", "text": "原来如此。", "delivery_mode": "visible_dialogue"}]
    draft = episode(turns=turns)
    for row in draft["chapter_script"]["shots"]:
        row["in_frame"] = ["甲", "乙"]
    script, plan = adopt(draft)
    clip = plan["clips"][0]
    assert "audio_delivery" not in clip
    assert clip["dialogue_bindings"][0]["inner_monologue"] is True
    assert [r["speaker_name"] for r in clip["lines"]] == ["甲", "乙"]
    assert script["shots"][0]["camera"] == "手部近景，平视固定。"
    assert clip["prompt_h3"] == draft["clips"][0]["prompt_h3"]


def test_prompt_patch_keeps_identity_words_references_and_timing():
    _, plan = adopt(episode())
    clip = plan["clips"][0]
    changed = clip["prompt_h3"].replace("relaxed grip", "firm grip")
    contracts.validate_patch(clip, changed)
    for before, after in [("你来啦。", "你走吧。"), ("<Picture 3>", "<Picture 4>"),
                          ("Planned duration: 15", "Planned duration: 14"), ("[Shot 1]", "[Shot 2]")]:
        with pytest.raises(ValueError):
            contracts.validate_patch(clip, clip["prompt_h3"].replace(before, after))


def test_format_collector_checks_all_clips_without_modifying_or_adopting_invalid_input():
    draft = episode()
    draft["clips"][0]["prompt_h3"] = draft["clips"][0]["prompt_h3"].replace("A steady breeze.", "No wind. 中文说明。")
    draft["clips"][-1]["prompt_h3"] = draft["clips"][-1]["prompt_h3"].replace("shown in <Picture 3>", "shown in <Picture 9>")
    saved = copy.deepcopy(draft)
    rows = contracts.collect_format_issues(draft, catalog=CATALOG, source_segments=SOURCES, profile={})
    assert [row["clip_id"] for row in rows] == ["clip_01", "clip_08"]
    assert len(rows[0]["request_issues"]) == 2
    assert rows[0]["cjk_excerpts"] == ["No wind. 中文说明。"]
    assert rows[0]["negative_excerpts"] == ["No wind. 中文说明。"]
    assert "Subject/Picture binding" in rows[1]["binding_or_timing_issue"]
    assert draft == saved
    with pytest.raises(ValueError):
        adopt(draft)


def test_explicit_episode_budget_and_backend_cap_allow_eighty_seconds_without_mutation():
    draft = episode()
    draft["chapter_script"]["shots"] = draft["chapter_script"]["shots"][:4]
    draft["clips"] = draft["clips"][:4]
    for row, clip in zip(draft["chapter_script"]["shots"], draft["clips"]):
        row["duration_seconds"] = 20
        clip.update(request_seconds=20, prompt_h3=prompt([row]))
    saved = copy.deepcopy(draft)
    inputs = {"catalog": CATALOG, "source_segments": SOURCES, "profile": {}}
    with pytest.raises(ValueError, match="3–15"):
        contracts.adopt(draft, **inputs)
    with pytest.raises(ValueError, match="120–150"):
        contracts.adopt(draft, clip_seconds_max=30, **inputs)
    for target in [(80, 80), (70, 90)]:
        script, plan = contracts.adopt(draft, target_seconds=target, clip_seconds_max=30, **inputs)
        assert plan["totals"]["requested_seconds"] == 80
        assert plan["limits"]["max_clip_seconds"] == 30
        assert len(script["shots"]) == 4
        assert contracts.collect_format_issues(draft, target_seconds=target, clip_seconds_max=30, **inputs) == []
    assert draft == saved
