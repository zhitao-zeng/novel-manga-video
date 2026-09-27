"""What the whole-take cast check finds is drawn again, not sent to a source recheck (agent ch12, 2026-09-26).

All 31 clips it failed were diagnosed "uncertain", and uncertain meant a source recheck that rewrote casts."""
from novel_manga.repair.policy import whole_take_decision

GOLD = {"cast_video": {"extra_person": False, "extra_object": False, "face_artifact": False, "note": "",
                       "looks": [{"name": "托尼·斯塔克", "costume_wrong": False, "color_wrong": True, "difference": "头盔是金色"}]}}


def test_a_wrong_look_is_retaken():
    decision = whole_take_decision(GOLD)
    assert decision.action == "retake" and decision.diagnosis["cause"] == "generation_mismatch"


def test_an_extra_person_is_retaken_and_a_repeat_is_rewritten():
    found = {"cast_video": {"extra_person": True, "note": "画面左侧多一个人", "looks": []}}
    assert whole_take_decision(found).action == "retake"
    assert whole_take_decision(found, repeated=True).action == "reframe"


def test_a_clean_or_unchecked_take_is_left_to_the_diagnosis():
    clean = {"cast_video": {"extra_person": False, "extra_object": False, "face_artifact": False,
                            "looks": [{"name": "席勒", "costume_wrong": False, "color_wrong": False}]}}
    assert whole_take_decision(clean) is None
    assert whole_take_decision({"cast_video": {"error": "connection refused"}}) is None
    assert whole_take_decision({}) is None
