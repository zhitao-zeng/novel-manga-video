import pytest

from novel_manga.multivoice import MultivoiceScript, subtitle_pages


def _script() -> dict:
    return {
        "video_id": "1_1",
        "voices": {
            "narrator": {"speaker": "Uncle_Fu"},
            "hero": {"speaker": "Dylan", "instruct": "少年男声"},
            "heroine": {"speaker": "Serena", "instruct": "温柔女声"},
        },
        "shots": [
            {"index": 1, "turns": [{"role": "narrator", "text": "故事开始。"}]},
            {"index": 2, "turns": [{"role": "hero", "text": "我会回来。"}]},
        ],
    }


def test_multivoice_script_counts_unique_speakers_and_turns():
    script = MultivoiceScript.model_validate(_script())
    assert script.speaker_count == 3
    assert script.turn_count == 2


def test_multivoice_script_rejects_unknown_role_and_nonconsecutive_shots():
    data = _script()
    data["shots"][0]["turns"][0]["role"] = "missing"
    with pytest.raises(ValueError, match="undefined roles"):
        MultivoiceScript.model_validate(data)

    data = _script()
    data["shots"][1]["index"] = 3
    with pytest.raises(ValueError, match="consecutive"):
        MultivoiceScript.model_validate(data)


def test_subtitle_pages_are_at_most_two_lines():
    pages = subtitle_pages("甲" * 80)
    assert len(pages) == 3
    assert all(page.count(r"\N") <= 1 for page in pages)
