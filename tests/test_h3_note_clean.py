"""A director correction reaches the H3 prompt in English even when the translator adds asides."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_h3_prompts import clean_note, tag_names  # noqa: E402

NAMING = "莱恩·格雷 = <Subject 1>\n琥珀·高德 = <Subject 2>\n"


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
    import build_h3_prompts as h3
    from build_h3_prompts import h3_source_digest
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
