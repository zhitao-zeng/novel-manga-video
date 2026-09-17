"""A flagged oddity the book itself describes is not a defect: script_check asks in text, fix_tier honours
the stored answer, the flag line says [剧本], and retier_reviews can apply it to a review already on disk."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import novel_manga.application.review.retier as retier_reviews
import novel_manga.llm.client as model_client
import novel_manga.review.policy as review_policy
import novel_manga.review.prompts as review_prompts
import novel_manga.application.review.judges as review_judges
from novel_manga.models.bible import Character, StoryBible

PROMPT = ("【生成目标】生成一段横屏短剧片段，约12秒。核心主体是达克尼斯，"
          "主要事件是从“掌心裂开一张嘴巴，露出利齿”到“邪教徒们面面相觑”。\n【人物】<达克尼斯>只对应@图片1")
SOURCE = "掌心的皮肤蠕动着裂开，那张可怕的嘴巴露了出来，随着嘴巴和利齿的翻动，它居然说话了"


def bible() -> StoryBible:
    return StoryBible(novel_title="雾月", genre="gaslamp", visual_style="3d", palette="p", style_fingerprint="f",
                      characters=[Character(name="达克尼斯", role="主角", appearance="青年", wardrobe="大衣")])


def verdict(**extra) -> dict:
    return {"severity": "fail", "identity_ok": False, "identity_issue": "达克尼斯的手掌中心出现了一张嘴巴和牙齿，属于严重的角色形象崩坏",
            "defect_issue": "", "visual_defects": True, **extra}


def test_event_line_is_read_from_the_prompt():
    assert review_prompts.scripted_event({"prompt": PROMPT}) == "从“掌心裂开一张嘴巴，露出利齿”到“邪教徒们面面相觑”"
    assert review_prompts.scripted_event({"prompt": "没有事件行"}) == ""
    assert review_prompts.scripted_event({}) == ""


def test_script_check_sends_event_source_and_complaint(monkeypatch):
    seen = {}

    def fake_ask(parts, schema, *, name, max_tokens=700, **_):
        seen["text"], seen["name"] = parts[0]["text"], name
        return {"scripted": True, "evidence": SOURCE[:12], "note": "原文写了"}

    monkeypatch.setattr(model_client, "ask_json", fake_ask)
    out = review_judges.script_check({"prompt": PROMPT, "segment_ids": ["seg_4"]}, verdict(), {"seg_4": SOURCE})
    assert out == {"scripted": True, "evidence": SOURCE[:12], "note": "原文写了"}
    assert seen["name"] == "script_check"
    for needle in ("掌心裂开一张嘴巴", "蠕动着裂开", "手掌中心出现了一张嘴巴"):
        assert needle in seen["text"]


def test_nothing_to_check_against_asks_nothing(monkeypatch):
    def refuse(*_, **__):
        raise AssertionError("must not ask")

    monkeypatch.setattr(model_client, "ask_json", refuse)
    assert review_judges.script_check({"prompt": "无", "segment_ids": []}, verdict(), {}) is None
    assert review_judges.script_check({"prompt": PROMPT, "segment_ids": []}, {"severity": "fail"}, {}) is None


def test_a_model_failure_changes_nothing(monkeypatch):
    def boom(*_, **__):
        raise RuntimeError("timeout")

    monkeypatch.setattr(model_client, "ask_json", boom)
    assert review_judges.script_check({"prompt": PROMPT, "segment_ids": []}, verdict(), {}) is None


def test_fix_tier_honours_a_scripted_verdict():
    assert review_policy.fix_tier(verdict(), bible()) == "must_fix"
    assert review_policy.fix_tier(verdict(scripted={"evidence": SOURCE[:12], "note": ""}), bible()) == "optional"
    assert review_policy.fix_tier(verdict(scripted=False), bible()) == "must_fix"


def test_flag_line_names_the_reason():
    v = {"identity_issue": "手掌中心出现嘴巴", "scripted": {"evidence": "x", "note": ""}}
    assert review_policy.flag_line("clip_04", v, "optional") == "clip_04: [剧本] 手掌中心出现嘴巴"
    assert review_policy.flag_line("clip_04", {"identity_issue": "x"}, "optional") == "clip_04: [可选] x"
    assert review_policy.flag_line("clip_04", {"identity_issue": "x"}, "must_fix") == "clip_04: x"


def test_retier_script_check_drops_a_scripted_retake(tmp_path, monkeypatch):
    novel = tmp_path / "wuyue"
    episode = novel / "wuyue_410"
    episode.mkdir(parents=True)
    (novel / "story_bible.json").write_text(bible().model_dump_json(), encoding="utf-8")
    (episode / "clip_plan.json").write_text(json.dumps({"clips": [
        {"clip_id": "clip_04", "kind": "video", "prompt": PROMPT, "segment_ids": ["seg_4"]},
        {"clip_id": "clip_05", "kind": "video", "prompt": PROMPT, "segment_ids": ["seg_5"]}]}, ensure_ascii=False))
    (episode / "segments.json").write_text(json.dumps([{"segment_id": "seg_4", "text": SOURCE},
                                                       {"segment_id": "seg_5", "text": "邪教徒们面面相觑"}], ensure_ascii=False))
    clone = verdict(identity_issue="画面中出现了两个一模一样的达克尼斯，角色重复", visual_defects=False)
    (episode / "episode_review.json").write_text(json.dumps({
        "policy": "x", "episode": "wuyue_410", "clips": {"clip_04": {**verdict(), "tier": "must_fix"},
                                                        "clip_05": {**clone, "tier": "must_fix"}},
        "flags": [], "feedback": {"clip_04": "修正达克尼斯的手掌，不要出现嘴巴", "clip_05": "达克尼斯只能出现一次"}},
        ensure_ascii=False), encoding="utf-8")

    def fake_ask(parts, schema, *, name, **_):
        text = parts[0]["text"]
        scripted = "嘴巴" in text and "蠕动着裂开" in text
        return {"scripted": scripted, "evidence": SOURCE[:12] if scripted else "", "note": "n"}

    monkeypatch.setattr(model_client, "ask_json", fake_ask)
    monkeypatch.setattr(sys, "argv", ["retier_reviews.py", "--novel-dir", str(novel), "--script-check", "--apply"])
    assert retier_reviews.main() == 0
    report = json.loads((episode / "episode_review.json").read_text(encoding="utf-8"))
    assert report["clips"]["clip_04"]["tier"] == "optional"
    assert report["clips"]["clip_04"]["scripted"] == {"evidence": SOURCE[:12], "note": "n"}
    assert report["clips"]["clip_05"]["tier"] == "must_fix" and report["clips"]["clip_05"]["scripted"] is False
    assert list(report["feedback"]) == ["clip_05"]
    assert report["flags"][0].startswith("clip_04: [剧本] ") and report["flags"][1].startswith("clip_05: ")
    assert (episode / "episode_review.json.bak-retier").is_file()

    # A second run asks nothing: both verdicts carry their answer.
    monkeypatch.setattr(model_client, "ask_json", lambda *_, **__: (_ for _ in ()).throw(AssertionError("asked again")))
    assert retier_reviews.main() == 0
