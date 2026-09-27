"""The whole take watched against the cast (agent ch12, 2026-09-26).

The frame review failed 3 of 11 clips a person could see were wrong, and its retakes traded a defect it saw for
one it did not.  What this check finds fails the clip; what it tells the retake is what to draw.
"""
from types import SimpleNamespace

from PIL import Image

from novel_manga.application.review import cast_video, judges
from novel_manga.review import policy

CLIP = {"clip_id": "clip_36", "cast": ["席勒", "托尼·斯塔克"], "extras": [],
        "prompt_h3": "subject_definitions:\n<Subject 1> is the person shown in <Picture 1>.\ndetailed_description:\n"
                     "[Shot 1] <Subject 1> and <Subject 2> talk in the lab.\noverall_soundscape:\nHum."}
DRAW = {"席勒": "黑发，戴细框眼镜，浅色衬衫外搭黑色马甲", "托尼·斯塔克": "棕色短发；全身穿着马克2机甲，面甲向上翻起"}
CLEAN = {"people": ["席勒", "托尼"], **{key: False for key in cast_video.FOUND}, "note": "无", "seen": [], "looks": []}
PASSED = {"severity": "pass", "identity_ok": True, "identity_issue": "", "feedback": "", "visual_defects": None,
          "verify": {"verdict": "fine"}}
GOLD = [{"name": "托尼·斯塔克", "costume_wrong": False, "color_wrong": True, "difference": "头盔是金色"},
        {"name": "席勒", "costume_wrong": False, "color_wrong": False, "difference": "无"}]


def test_a_clean_take_keeps_its_verdict_and_records_the_check():
    merged = cast_video.merge(PASSED, CLEAN, CLIP, DRAW)
    assert merged["severity"] == "pass" and merged["verify"]["cast_video"] == CLEAN


def test_a_stranger_fails_the_clip_and_the_retake_is_told_what_to_draw_not_what_went_wrong():
    merged = cast_video.merge(PASSED, {**CLEAN, "extra_person": True, "note": "画面左侧多出一个穿棕色外套的男子"}, CLIP, DRAW)
    assert merged["severity"] == "fail" and merged["identity_ok"] is False
    assert "棕色外套" in merged["identity_issue"]                     # the report says what was seen
    feedback = policy.compose_feedback(merged)
    assert "只有席勒、托尼·斯塔克" in feedback and "棕色外套" not in feedback  # the retake is not told to draw it
    assert policy.fix_tier(merged, SimpleNamespace(characters=[])) == "must_fix"


def test_a_wrong_look_names_only_whoever_is_wrong_and_what_they_should_wear():
    merged = cast_video.merge(PASSED, {**CLEAN, "looks": GOLD}, CLIP, DRAW)
    assert merged["severity"] == "fail" and "托尼·斯塔克：头盔是金色" in merged["identity_issue"]
    feedback = policy.compose_feedback(merged)
    assert "托尼·斯塔克的发型和衣着与参考图完全一致：棕色短发；全身穿着马克2机甲" in feedback
    assert "金色" not in feedback and "席勒的发型" not in feedback


def test_a_face_artefact_adds_the_structure_rule():
    merged = cast_video.merge(PASSED, {**CLEAN, "face_artifact": True, "note": "脸上流下黑色液体"}, CLIP, DRAW)
    assert merged["visual_defects"] and "结构正常" in policy.compose_feedback(merged)


def test_the_question_carries_the_shot_and_the_extras():
    text = cast_video.question({**CLIP, "extras": ["公交司机"]}, DRAW, "")
    assert "talk in the lab" in text and "背景里允许出现：公交司机" in text and "托尼·斯塔克：棕色短发" in text


def test_the_look_is_described_blind_and_compared_in_text(monkeypatch, tmp_path):
    """The video question about clothes says nothing about the cards; the comparison sees no picture."""
    asked = {}

    def ask(parts, schema, name):
        asked[name] = parts
        return {"clip_cast_video": dict(CLEAN), "clip_describe": {"people": [{"who": "穿盔甲的人", "wears": "银灰盔甲，头盔是金色"}]},
                "clip_look_compare": {"checks": [{**c, "kind": "color" if c["color_wrong"] else "none",
                    "same_entity": True, "visibility": "visible", "same_color_family": False} for c in GOLD]}}[name]

    monkeypatch.setattr(cast_video, "ask", ask)
    monkeypatch.setattr(cast_video, "video_part", lambda video: {"type": "video_url", "video_url": {"url": "data:,"}})
    monkeypatch.setattr(cast_video, "looks", lambda clip, novel_dir, episode_dir: DRAW)
    monkeypatch.setattr(cast_video, "watched", lambda clip, novel_dir: ["托尼·斯塔克：他的面甲或头盔是不是被画成了金色"])
    answer, _ = cast_video.check(CLIP, tmp_path / "v.mp4", tmp_path / "book" / "book_12" / "work" / "review" / "clip_36")
    described = asked["clip_describe"][-1]["text"]                  # nothing said about what to expect
    assert all(look not in described for look in DRAW.values()) and "黑色马甲" not in described
    assert [p["type"] for p in asked["clip_look_compare"]] == ["text"]  # no picture to agree with
    assert "头盔是不是被画成了金色" in asked["clip_look_compare"][0]["text"]
    assert cast_video.findings(answer) == ["look_wrong"]


def test_the_watch_list_never_reaches_the_retake(monkeypatch, tmp_path):
    card = tmp_path / "c2" / "turnaround.jpeg"
    card.parent.mkdir()
    Image.new("RGB", (32, 48), "silver").save(card)
    (tmp_path / "review_watch.txt").write_text("# for the judge\n托尼·斯塔克：他的面甲是不是金色\n", encoding="utf-8")
    monkeypatch.setattr(cast_video, "card_look", lambda novel_dir, name, path: "银白色盔甲，面甲打开")
    clip = {"cast": ["托尼·斯塔克"], "references": [{"role": "character", "name": "托尼·斯塔克", "path": "c2/turnaround.jpeg"}]}
    assert cast_video.watched(clip, tmp_path) == ["托尼·斯塔克：他的面甲是不是金色"]
    draw = cast_video.looks(clip, tmp_path, tmp_path / "book_12")
    merged = cast_video.merge(PASSED, {**CLEAN, "looks": GOLD[:1]}, clip, draw)
    assert "银白色盔甲" in merged["feedback"] and "金色" not in merged["feedback"]


def test_the_check_runs_only_when_switched_on(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(judges, "judge_clip_verify", lambda *a: dict(PASSED))
    monkeypatch.setattr(judges.review_evidence, "review_mode", lambda work_dir: "verify")
    monkeypatch.setattr(cast_video, "check",
                        lambda *a: calls.append(1) or ({**CLEAN, "extra_person": True, "note": "多一个人"}, DRAW))
    monkeypatch.delenv("NOVEL_REVIEW_CAST_VIDEO", raising=False)
    assert judges.judge_clip(CLIP, tmp_path / "v.mp4", None, {}, "", tmp_path)["severity"] == "pass" and calls == []
    monkeypatch.setenv("NOVEL_REVIEW_CAST_VIDEO", "1")
    assert judges.judge_clip(CLIP, tmp_path / "v.mp4", None, {}, "", tmp_path)["severity"] == "fail" and calls == [1]


def test_an_enabled_check_that_cannot_complete_is_not_a_pass(monkeypatch, tmp_path):
    def down(*a):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(cast_video, "check", down)
    merged = cast_video.review(CLIP, tmp_path / "v.mp4", tmp_path, dict(PASSED))
    assert merged["severity"] == "review_error" and "error" in merged["verify"]["cast_video"]


def test_the_card_is_read_into_words_once_per_picture(monkeypatch, tmp_path):
    card = tmp_path / "series_assets/characters/character_001/turnaround.jpeg"
    card.parent.mkdir(parents=True)
    Image.new("RGB", (32, 48), "black").save(card)
    asked = []
    monkeypatch.setattr(cast_video.model_client, "ask_json",
                        lambda parts, schema, **kw: asked.append(kw["name"]) or {"look": "浅色衬衫、黑色马甲，没有外套和领带"})
    assert cast_video.card_look(tmp_path, "席勒", card) == "浅色衬衫、黑色马甲，没有外套和领带"
    assert cast_video.card_look(tmp_path, "席勒", card) == "浅色衬衫、黑色马甲，没有外套和领带" and asked == ["card_look"]
    Image.new("RGB", (32, 48), "white").save(card)            # another picture is read again
    cast_video.card_look(tmp_path, "席勒", card)
    assert asked == ["card_look", "card_look"]


def test_the_closed_faceplate_view_is_not_taken_for_the_characters_card(tmp_path):
    (tmp_path / "c2").mkdir()
    for name in ("turnaround.jpeg", "closed.jpeg"):
        (tmp_path / "c2" / name).write_bytes(b"x")
    clip = {"cast": ["托尼·斯塔克"], "references": [
        {"role": "character", "name": "托尼·斯塔克", "path": "c2/closed.jpeg"},
        {"role": "character", "name": "托尼·斯塔克", "path": "c2/turnaround.jpeg"}]}
    assert cast_video.cast_cards(clip, tmp_path) == {"托尼·斯塔克": tmp_path / "c2" / "turnaround.jpeg"}
