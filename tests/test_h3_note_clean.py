"""A director correction reaches the H3 prompt in English even when the translator adds asides."""
from __future__ import annotations

import sys
from pathlib import Path

from novel_manga.story.h3 import clean_note, tag_names  # noqa: E402

NAMING = "莱恩·格雷 = <Subject 1>\n琥珀·高德 = <Subject 2>\n"


def test_performance_notes_do_not_replace_the_legacy_speaker():
    from novel_manga.story.h3 import stages_of, compose
    prompt = '【阶段1】洛恩站在门边。声音：中文普通话，平静，眉头微皱，眼神低垂，尾巴轻摆。，洛恩开口说：{我们走吧。}结束时：门打开。画面呈现'
    stages = stages_of(prompt)
    assert stages[0][1] == [('洛恩', '我们走吧。', False)]
    clip = {'references': [{'role': 'character', 'name': '洛恩'}], 'request_seconds': 15}
    request = compose(clip, ['<Subject 1> opens the door.'], stages)
    assert '<Subject 1> (S1) says <d>[Chinese] 我们走吧。</d>' in request
    assert 'An off-screen voice' not in request


def test_translation_receives_current_character_traits(monkeypatch):
    from novel_manga.application.rendering import h3
    calls = []
    def answer(parts, schema, **kwargs):
        calls.append(parts[0]['text'])
        return {'shots': ['<Subject 1> stands by a doorway.']}
    monkeypatch.setattr(h3, 'ask_json', answer)
    clip = {'clip_id': 'c', 'request_seconds': 15,
            'prompt': '【人物】\n阿甲的辨识特征：人类少年，黑发。\n【阶段1】阿甲站在门边。画面呈现',
            'references': [{'role': 'character', 'name': '阿甲'}]}
    assert h3.convert(clip)
    assert '阿甲 = <Subject 1> (人类少年，黑发)' in calls[0]
    assert '<Subject 1> stands by a doorway.' in clip['prompt_h3']
    assert not h3.convert(clip) and len(calls) == 1


def test_commentary_with_a_chinese_name_is_reduced_to_the_instruction():
    text = ("The original text contains a character name (莱恩·格雷) that is not in the provided tag list. "
            "I have translated it as <Subject 1>. <Subject 2> must be an orange tabby cat, not a man in a suit; "
            "<Subject 1> stays as he is.")
    out = clean_note(text, NAMING)
    assert "<Subject 2> must be an orange tabby cat" in out and "<Subject 1> stays" in out
    assert "莱恩" not in out and "tag list" not in out and "translated" not in out


def test_a_tagged_name_becomes_its_tag_and_a_clean_note_is_unchanged():
    assert clean_note("Keep 琥珀·高德 as a cat.", NAMING) == "Keep <Subject 2> as a cat."
    assert clean_note("Keep 琥珀 as a cat.", NAMING) == "Keep <Subject 2> as a cat."
    assert clean_note("Remove the extra man beside <Subject 1>.", NAMING) == "Remove the extra man beside <Subject 1>."


def test_all_chinese_still_fails():
    assert clean_note("请把琥珀画成猫", NAMING) == ""


def test_names_are_tagged_before_the_ask():
    assert tag_names("请修正角色琥珀·高德的形象，保持莱恩的形象不变", NAMING) == "请修正角色<Subject 2>的形象，保持<Subject 1>的形象不变"


def test_meta_commentary_is_dropped():
    text = "The user's request contains a contradiction. <Subject 2> must be an orange tabby cat, not a man in a suit."
    assert clean_note(text, NAMING) == "<Subject 2> must be an orange tabby cat, not a man in a suit."


def test_a_folded_correction_is_merged_into_the_shots(monkeypatch):
    import novel_manga.application.rendering.h3 as h3
    from novel_manga.application.rendering.h3 import h3_source_digest
    prompt = "【阶段1】林凡推门走进大殿。"
    note = "林凡必须是一只橘色的猫"
    clip = {"clip_id": "clip_01", "kind": "video", "prompt": prompt, "request_seconds": 10, "references": [],
            "cast": ["林凡"], "shots": []}
    asked = []
    answers = iter([{"shots": ["A cat pushes the door open."]},                     # folded: one sentence for two lines
                    {"shots": ["A cat pushes the door open, an orange cat."]}])     # merged ask: one sentence per shot
    def fake(question, schema, **kwargs):
        asked.append(question[0]["text"]); return next(answers)
    monkeypatch.setattr(h3, "ask_json", fake)
    assert h3.convert(clip, note=note)
    assert len(asked) == 2 and "导演修正" in asked[1] and "导演修正" not in asked[0]
    assert "director_note" not in clip["prompt_h3"] and "orange cat" in clip["prompt_h3"]
    assert clip["prompt_h3_of"] == h3_source_digest(prompt, note)


def test_compose_follows_the_official_six_sections():
    import novel_manga.application.rendering.h3 as bh
    clip = {"request_seconds": 10, "references": [
        {"role": "character", "name": "莱恩·格雷", "path": "a"}, {"role": "character", "name": "比尔·维克托", "path": "b"},
        {"role": "location", "name": "书房", "path": "c"}, {"role": "voice", "name": "莱恩·格雷", "path": "v"}]}
    stages = [(None, [("莱恩·格雷", "你来了", False), ("比尔·维克托", "我在门外", True), ("", "旁白句", True)])]
    text = bh.compose(clip, ["<Subject 1> stands by the door."], stages, note="<Subject 1> stands alone by the door.")
    assert "director_note" not in text and "Direction for this take: <Subject 1> stands alone" in text
    assert "Exactly one <Subject 1> appears" in text and "<Subject 1>: fully_preserved" in text
    assert "<Subject 1> (S1) says <d>[Chinese] 你来了</d>" in text
    assert "<Subject 2> (S2) says in an off-screen voiceover <d>[Chinese] 我在门外</d> The on-screen characters' lips remain closed." in text
    assert "An off-screen voice (S3) says in an off-screen voiceover" in text
    order = [text.index(k) for k in ("subject_definitions:", "summary:", "retention_analysis:", "detailed_description:", "overall_soundscape:", "non_diegetic_music:")]
    assert order == sorted(order)
