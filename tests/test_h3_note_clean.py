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
