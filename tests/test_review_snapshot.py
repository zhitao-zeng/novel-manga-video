"""The clip judge gets the ledger's casting sheet for the clip's passage - and nothing at all when the novel has
no ledger for that chapter."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import entity_ledger_thin as el  # noqa: E402
import thin_review  # noqa: E402

CH7 = "薇奥拉公主走进来。“早安，调查师。”作家小姐笑着说。莱恩点头。原来作家小姐就是薇奥拉。脑内的女声说：别信她。"


def test_snapshot_block_names_the_cast_and_stays_silent_without_a_ledger(tmp_path, monkeypatch):
    root = tmp_path / "wuyue"
    episode = root / "wuyue_7"
    episode.mkdir(parents=True)
    (episode / "segments.json").write_text(json.dumps([{"segment_id": "seg_1", "text": CH7}], ensure_ascii=False), encoding="utf-8")
    (root / "story_bible.json").write_text(json.dumps({"characters": [{"name": "莱恩·格雷", "role": "主角"}, {"name": "薇奥拉公主", "role": "公主"}]},
                                                      ensure_ascii=False), encoding="utf-8")
    clip = {"segment_ids": ["seg_1"]}
    assert thin_review.snapshot_block(clip, episode) == ""          # no ledger yet: the judge prompt is unchanged
    monkeypatch.setattr(el, "judge_claim", lambda claim, s, o: {"verdict": "supports", "why": ""})
    el.Ledger(root).resolve_chapter(7, CH7, {"chapter": 7, "mentions": [
        {"form": "薇奥拉公主", "entity": "e002", "kind": "proper", "presence": "on_stage", "evidence": "薇奥拉公主走进来"},
        {"form": "作家小姐", "entity": "e002", "kind": "proper", "presence": "on_stage", "evidence": "作家小姐笑着说"},
        {"form": "莱恩", "entity": "e001", "kind": "proper", "presence": "on_stage", "evidence": "莱恩点头"},
        {"form": "女声", "entity": "NEW:脑内女声", "kind": "contextual", "presence": "voice", "evidence": "脑内的女声说"}],
        "new_entities": [{"name": "脑内女声", "kind": "spirit", "named": False, "description": "声音", "evidence": "脑内的女声说：别信她"}],
        "claims": [], "relations": [{"from": "e001", "to": "e002", "base": "unknown", "stance": "romantic_interest", "address": "殿下",
                                     "hidden_from_reader": False, "evidence": "莱恩点头"}]}, workers=1)
    thin_review._LEDGER_CACHE.clear()
    thin_review._NOVEL_TEXT_CACHE.clear()
    block = thin_review.snapshot_block(clip, episode)
    assert "在场 薇奥拉公主（原文里也叫 作家小姐）、莱恩·格雷" in block or "在场 莱恩·格雷、薇奥拉公主（原文里也叫 作家小姐）" in block
    assert "只有声音 脑内女声" in block and "莱恩·格雷→薇奥拉公主：romantic_interest（称呼 殿下）" in block
    assert "观众尚不能知道" not in block


def test_verify_answer_maps_to_the_review_shape():
    import thin_review as tr
    obvious = tr.verify_to_verdict({"people": [{"who": "猫", "gender": "不明", "is_animal": True, "doing": "亲莱恩", "frames": "3-6"}],
                                    "same_person_twice": False, "species_or_gender_wrong": False, "action_by_wrong_person": True, "actor_missing": True,
                                    "lead_face_swapped": False, "ghost_text": False, "verdict": "obvious", "evidence": "图3-6 猫代替薇奥拉亲莱恩"})
    assert obvious["story_ok"] is False and obvious["story_kind"] == "动作落在错误的人物身上" and obvious["severity"] == "fail"
    assert obvious["feedback"].startswith("按原文修正剧情：") and obvious["verify"]["action_by_wrong_person"]
    assert tr.fix_tier(obvious, tr.StoryBible(novel_title="t", genre="g", visual_style="v", palette="p", style_fingerprint="f", characters=[], locations=[])) == "must_fix"
    subtle = tr.verify_to_verdict({"people": [], "same_person_twice": False, "species_or_gender_wrong": False, "action_by_wrong_person": False, "actor_missing": False,
                                   "lead_face_swapped": False, "ghost_text": True, "verdict": "subtle", "evidence": "发色偏棕"})
    assert subtle["story_ok"] is True and subtle["identity_ok"] is False and subtle["severity"] == "minor" and subtle["text_or_watermark"] is True
    fine = tr.verify_to_verdict({"people": [], "same_person_twice": False, "species_or_gender_wrong": False, "action_by_wrong_person": False, "actor_missing": False,
                                 "lead_face_swapped": False, "ghost_text": False, "verdict": "fine", "evidence": ""})
    assert fine["severity"] == "pass" and fine["story_kind"] == "无问题"


def test_review_mode_comes_from_env_or_profile(monkeypatch, tmp_path):
    import thin_review as tr
    work = tmp_path / "novel" / "ep_1" / "work" / "review" / "clip_01"; work.mkdir(parents=True)
    monkeypatch.delenv("NOVEL_REVIEW_MODE", raising=False)
    assert tr.review_mode(work) == ""
    (tmp_path / "novel" / "profile.json").write_text('{"review_mode": "verify"}', encoding="utf-8")
    assert tr.review_mode(work) == "verify"
    monkeypatch.setenv("NOVEL_REVIEW_MODE", "classic")
    assert tr.review_mode(work) == "classic"
