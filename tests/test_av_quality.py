from novel_manga.av_quality import (
    STATUS_FAILED,
    STATUS_PASSED,
    _band_difference,
    evaluate_asr,
)


def _plan() -> dict:
    return {
        "units": [
            {
                "unit_id": "shot_001_turn_01",
                "shot_index": 1,
                "turn_index": 1,
                "role": "narrator",
                "speaking": False,
                "text": "旁白。",
            },
            {
                "unit_id": "shot_001_turn_02",
                "shot_index": 1,
                "turn_index": 2,
                "role": "xiaoyan",
                "speaking": True,
                "text": "我会回来。",
            },
        ]
    }


def test_asr_gate_does_not_hide_bad_turn_behind_good_average() -> None:
    report = {
        "recognizer": "test",
        "cer": 0.05,
        "turns": [
            {"shot_index": 1, "turn_index": 1, "reference": "旁白。", "hypothesis": "旁白", "cer": 0.0},
            {"shot_index": 1, "turn_index": 2, "reference": "我会回来。", "hypothesis": "", "cer": 1.0},
        ],
    }
    result = evaluate_asr(_plan(), report)
    assert result["status"] == STATUS_FAILED
    assert [item["unit_id"] for item in result["bad_turns"]] == ["shot_001_turn_02"]


def test_subtitle_band_difference_is_localized() -> None:
    left = bytes([0] * 100)
    right = bytearray(left)
    for index in range(50, 100):
        right[index] = 30
    top = _band_difference(left, bytes(right), width=10, y_start=0, y_end=5)
    bottom = _band_difference(left, bytes(right), width=10, y_start=5, y_end=10)
    assert top["changed_ratio"] == 0.0
    assert bottom["changed_ratio"] == 1.0
