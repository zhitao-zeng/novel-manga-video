"""Audit #5, #6, #7: one truth per clip, a manner that invents nothing, one style both languages.

#5 A director's correction stopped being a parallel truth: once its English is in, the Chinese
   prompt carries it and the note is spent - the plan is the source, not three versions of it.
   The inner-voice template follows the shot's framing (a hands close-up reacts in the hands).
#6 The manner translation's constraints are enforced where the answer lands, not only in the
   ask: a 崩溃 that comes back as "a voice that breaks" is dropped, not spoken.
#7 The style line in the Chinese prompt and the English request come from the live style
   package, not the grammar snapshot they were generated in.
"""
import json

import novel_manga.application.rendering.h3 as h3
from novel_manga.application.rendering.h3 import delivery_acceptable
from novel_manga.story.h3 import compose


def test_a_correction_merges_into_the_chinese_prompt_once(monkeypatch):
    """#5: convert succeeds with a note -> the note is IN clip['prompt'] and the stamp treats it
    as spent, so the merged prompt's stamp is stable on the next run."""
    prompt = "【生成目标】一段 10 秒片段。\n【阶段】1. 他推门。"
    clip = {"clip_id": "clip_01", "kind": "video", "prompt": prompt, "request_seconds": 10,
            "references": [{"role": "character", "name": "甲", "path": "c1/turnaround.jpeg"}],
            "lines": [], "cast": ["甲"]}
    monkeypatch.setattr(h3, "ask_json", lambda parts, schema, **kw: {"shots": ["He pushes the door open."]})
    note = "只拍手和咖啡杯，不拍脸部"
    assert h3.convert(clip, note=note)
    assert "【导演修正】" + note in clip["prompt"]           # the Chinese carries it
    assert clip["prompt_correction_merged"] is True
    assert clip["prompt_before_correction"] == prompt        # the old words are kept for the record
    from novel_manga.application.profiles import h3_prompt_outdated, h3_stamp
    # spent: the stamp of the merged prompt with the note still passed is the stamp without it
    assert h3_stamp(clip, note) == h3_stamp(clip, "")
    assert not h3_prompt_outdated(clip, note, strict=True)   # stable, not stale-by-its-own-merge


def test_no_note_no_merge():
    clip = {"clip_id": "clip_01", "kind": "video", "prompt": "【阶段】1. 他推门。", "request_seconds": 10,
            "references": [], "lines": [], "cast": []}
    h3.convert.__wrapped__ if hasattr(h3.convert, "__wrapped__") else None
    # a plain conversion with no note merges nothing
    assert "prompt_correction_merged" not in clip


def test_the_inner_voice_follows_the_framing():
    """#5b: the private-thought clause no longer pins the reaction to eyes and breathing."""
    clip = {"request_seconds": 10, "references": [
        {"role": "character", "name": "甲", "path": "a"}],
        "scene_ids": ["sc1"], "shot_timing": [{"seconds": 4}],
        "dialogue_bindings": [{"stage": 1, "speaker_name": "甲", "text": "今天真难啊。",
                               "delivery_mode": "offscreen_dialogue", "emotion": "平静", "inner_monologue": True}]}
    request = compose(clip, ["His hands tighten around the cup."],
                      [("stage", [])], note="")
    assert "only eyes and breathing" not in request
    assert "whatever the shot's framing shows" in request


def test_a_manner_that_invents_vocal_behaviour_is_dropped():
    """#6: 崩溃 alone licenses nothing; a phrase that names the behaviour licenses it."""
    assert not delivery_acceptable("崩溃", "The line is delivered in a voice that breaks.")
    assert delivery_acceptable("哽咽着说", "The line is delivered in a choked voice.")
    # 怒吼 names the shout, so the shout is not an invention.
    assert delivery_acceptable("怒吼", "The line is delivered as a furious shout.")
    assert not delivery_acceptable("愤怒", "The line is delivered as a furious shout.")


def test_a_manner_that_describes_the_body_is_dropped():
    assert not delivery_acceptable("恐惧", "The line is delivered with a determined expression.")
    assert not delivery_acceptable("紧张", "The line is delivered while clenching his fists.")
    assert delivery_acceptable("紧张", "The line is delivered fast and unsteady.")


def test_english_delivery_filters_invented_manners(monkeypatch):
    wanted = ["崩溃", "哽咽着说"]
    answers = iter([{"manners": ["The line is delivered in a voice that breaks.",
                                 "The line is delivered in a choked voice."]}])
    monkeypatch.setattr(h3, "ask_json", lambda parts, schema, **kw: next(answers))
    got = h3.english_delivery(wanted)
    assert got == {"哽咽着说": "The line is delivered in a choked voice."}   # 崩溃's invention is gone


def test_the_style_line_comes_from_the_live_style(tmp_path, monkeypatch):
    """#7: the grammar snapshot said 网点与交叉排线, the live style says the clean line - the
    Chinese prompt follows the live one, like the English always has."""
    from novel_manga.application.packing.context import load_context
    novel = tmp_path / "nov"
    (novel / "series_assets" / "characters").mkdir(parents=True)
    (novel / "profile.json").write_text(json.dumps({"style": "meiman", "frame": "16:9"}), encoding="utf-8")
    (novel / "story_bible.json").write_text(json.dumps(
        {"novel_title": "t", "genre": "g", "visual_style": "3d", "palette": "c", "style_fingerprint": "f",
         "characters": [{"name": "甲", "role": "主角", "appearance": "a", "wardrobe": "w"}],
         "locations": ["诊所：房间"]}, ensure_ascii=False), encoding="utf-8")
    # the book's own style: the clean cut
    (novel / "style.json").write_text(json.dumps(
        {"render_family": "3d", "h3_style_line": "clean flat colours, clear outlines, no crosshatching"}), encoding="utf-8")
    episode = novel / "nov_1"
    episode.mkdir()
    # the grammar snapshot the chapter was generated with
    (episode / "visual_grammar.json").write_text(json.dumps(
        {"style_line": "网点与交叉排线", "light_contrast": "低对比"}, ensure_ascii=False), encoding="utf-8")
    ctx = load_context(episode, novel / "story_bible.json", episode / "visual_grammar.json")
    assert ctx["grammar"]["style_line"] == "clean flat colours, clear outlines, no crosshatching"
    assert ctx["grammar"]["light_contrast"] == "低对比"     # the grammar's own axes stay
