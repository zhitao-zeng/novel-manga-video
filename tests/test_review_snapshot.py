"""The clip judge gets the ledger's casting sheet for the clip's passage - and nothing at all when the novel has
no ledger for that chapter."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import entity_ledger_thin as el  # noqa: E402
import novel_manga.models as review_models
import novel_manga.review.policy as review_policy
import review_episode_thin as review_episode
import review_evidence_thin as review_evidence
import review_judges_thin as review_judges  # noqa: E402

CH7 = "薇奥拉公主走进来。“早安，调查师。”作家小姐笑着说。莱恩点头。原来作家小姐就是薇奥拉。脑内的女声说：别信她。"


def test_snapshot_block_names_the_cast_and_stays_silent_without_a_ledger(tmp_path, monkeypatch):
    root = tmp_path / "wuyue"
    episode = root / "wuyue_7"
    episode.mkdir(parents=True)
    (episode / "segments.json").write_text(json.dumps([{"segment_id": "seg_1", "text": CH7}], ensure_ascii=False), encoding="utf-8")
    (root / "story_bible.json").write_text(json.dumps({"characters": [{"name": "莱恩·格雷", "role": "主角"}, {"name": "薇奥拉公主", "role": "公主"}]},
                                                      ensure_ascii=False), encoding="utf-8")
    clip = {"segment_ids": ["seg_1"]}
    assert review_evidence.snapshot_block(clip, episode) == ""          # no ledger yet: the judge prompt is unchanged
    monkeypatch.setattr(el, "judge_claim", lambda claim, s, o: {"verdict": "supports", "why": ""})
    el.Ledger(root).resolve_chapter(7, CH7, {"chapter": 7, "mentions": [
        {"form": "薇奥拉公主", "entity": "e002", "kind": "proper", "presence": "on_stage", "evidence": "薇奥拉公主走进来"},
        {"form": "作家小姐", "entity": "e002", "kind": "proper", "presence": "on_stage", "evidence": "作家小姐笑着说"},
        {"form": "莱恩", "entity": "e001", "kind": "proper", "presence": "on_stage", "evidence": "莱恩点头"},
        {"form": "女声", "entity": "NEW:脑内女声", "kind": "contextual", "presence": "voice", "evidence": "脑内的女声说"}],
        "new_entities": [{"name": "脑内女声", "kind": "spirit", "named": False, "description": "声音", "evidence": "脑内的女声说：别信她"}],
        "claims": [], "relations": [{"from": "e001", "to": "e002", "base": "unknown", "stance": "romantic_interest", "address": "殿下",
                                     "hidden_from_reader": False, "evidence": "莱恩点头"}]}, workers=1)
    review_evidence._LEDGER_CACHE.clear()
    review_evidence._NOVEL_TEXT_CACHE.clear()
    block = review_evidence.snapshot_block(clip, episode)
    assert "在场 薇奥拉公主（原文里也叫 作家小姐）、莱恩·格雷" in block or "在场 莱恩·格雷、薇奥拉公主（原文里也叫 作家小姐）" in block
    assert "只有声音 脑内女声" in block and "莱恩·格雷→薇奥拉公主：romantic_interest（称呼 殿下）" in block
    assert "观众尚不能知道" not in block


def test_verify_answer_maps_to_the_review_shape():
    import review_judges_thin as review_judges
    obvious = review_policy.verify_to_verdict({"people": [{"who": "猫", "gender": "不明", "is_animal": True, "doing": "亲莱恩", "frames": "3-6"}],
                                    "same_person_twice": False, "species_or_gender_wrong": False, "action_by_wrong_person": True, "actor_missing": True,
                                    "lead_face_swapped": False, "ghost_text": False, "verdict": "obvious", "evidence": "图3-6 猫代替薇奥拉亲莱恩"})
    assert obvious["story_ok"] is False and obvious["story_kind"] == "动作落在错误的人物身上" and obvious["severity"] == "fail"
    assert obvious["feedback"].startswith("按原文修正剧情：") and obvious["verify"]["action_by_wrong_person"]
    assert review_policy.fix_tier(obvious, review_models.StoryBible(novel_title="t", genre="g", visual_style="v", palette="p", style_fingerprint="f", characters=[], locations=[])) == "must_fix"
    subtle = review_policy.verify_to_verdict({"people": [], "same_person_twice": False, "species_or_gender_wrong": False, "action_by_wrong_person": False, "actor_missing": False,
                                   "lead_face_swapped": False, "ghost_text": True, "verdict": "subtle", "evidence": "发色偏棕"})
    assert subtle["story_ok"] is True and subtle["identity_ok"] is False and subtle["severity"] == "minor" and subtle["text_or_watermark"] is True
    fine = review_policy.verify_to_verdict({"people": [], "same_person_twice": False, "species_or_gender_wrong": False, "action_by_wrong_person": False, "actor_missing": False,
                                 "lead_face_swapped": False, "ghost_text": False, "verdict": "fine", "evidence": ""})
    assert fine["severity"] == "pass" and fine["story_kind"] == "无问题"


def test_review_mode_comes_from_env_or_profile(monkeypatch, tmp_path):
    import review_judges_thin as review_judges
    work = tmp_path / "novel" / "ep_1" / "work" / "review" / "clip_01"; work.mkdir(parents=True)
    monkeypatch.delenv("NOVEL_REVIEW_MODE", raising=False)
    assert review_evidence.review_mode(work) == ""
    (tmp_path / "novel" / "profile.json").write_text('{"review_mode": "verify"}', encoding="utf-8")
    assert review_evidence.review_mode(work) == "verify"
    monkeypatch.setenv("NOVEL_REVIEW_MODE", "classic")
    assert review_evidence.review_mode(work) == "classic"


def test_unchanged_takes_keep_their_verdict_across_reviews(monkeypatch, tmp_path):
    """Two reviews of the same episode: the second judges only the clip whose file changed."""
    import json
    import review_judges_thin as review_judges
    novel = tmp_path / "n"; ep = novel / "n_1"; (ep / "work" / "clips" / "clip_01" / "attempt_01").mkdir(parents=True)
    (ep / "work" / "clips" / "clip_02" / "attempt_01").mkdir(parents=True)
    (novel / "story_bible.json").write_text(json.dumps({"novel_title": "n", "genre": "g", "visual_style": "v", "palette": "p", "style_fingerprint": "f", "characters": [], "locations": []}), encoding="utf-8")
    (ep / "clip_plan.json").write_text(json.dumps({"clips": [{"clip_id": "clip_01", "kind": "video"}, {"clip_id": "clip_02", "kind": "video"}]}), encoding="utf-8")
    for cid in ("clip_01", "clip_02"):
        (ep / "work" / "clips" / cid / "attempt_01" / "clip.mp4").write_bytes(b"take one " + cid.encode())
    judged = []

    def fake_judge(clip, video, bible, location_time, hypothesis, work_dir):
        judged.append(clip["clip_id"])
        return {"visible_people": 1, "identity_ok": True, "identity_issue": "", "location_ok": True, "time_of_day_ok": True, "location_issue": "",
                "text_or_watermark": False, "chat_text_ok": True, "chat_text_issue": "", "visual_defects": False, "defect_issue": "",
                "story_ok": True, "story_kind": "无问题", "story_issue": "", "severity": "pass", "feedback": ""}
    monkeypatch.setattr(review_judges, "judge_clip", fake_judge)
    monkeypatch.setattr(review_judges, "script_check", lambda *a, **k: None, raising=False)
    monkeypatch.delenv("NOVEL_REVIEW_FRESH", raising=False)
    review_episode.review_episode(ep)
    assert sorted(judged) == ["clip_01", "clip_02"]
    (ep / "work" / "clips" / "clip_02" / "attempt_01" / "clip.mp4").write_bytes(b"take two, longer bytes")
    judged.clear()
    review_episode.review_episode(ep)
    assert judged == ["clip_02"]
    monkeypatch.setenv("NOVEL_REVIEW_FRESH", "1")
    judged.clear()
    review_episode.review_episode(ep)
    assert sorted(judged) == ["clip_01", "clip_02"]


def test_story_feedback_is_the_judges_instruction_not_the_description():
    import review_judges_thin as review_judges
    verdict = {"story_ok": False, "story_kind": "动作落在错误的人物身上", "story_issue": "画面里两个一模一样的艾琳娜坐在左右两侧",
               "feedback": "艾琳娜只出现一次，坐在桌子左侧读信；莱恩·格雷站在她对面", "identity_ok": True}
    note = review_policy.compose_feedback(verdict)
    assert note.startswith("艾琳娜只出现一次") and "一模一样" not in note
    verdict["feedback"] = ""
    assert review_policy.compose_feedback(verdict).startswith("按原文修正剧情：")
    answer = {"people": [], "same_person_twice": True, "species_or_gender_wrong": False, "action_by_wrong_person": False, "actor_missing": False,
              "lead_face_swapped": False, "ghost_text": False, "verdict": "obvious", "evidence": "两个艾琳娜", "instruction": "艾琳娜只出现一次"}
    assert review_policy.verify_to_verdict(answer)["feedback"] == "艾琳娜只出现一次"
