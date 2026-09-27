"""How each line is spoken, traced from the author's sheet to the request H3 receives.

test_authored_sheet_to_request.py follows the words, their owner and the manner; nothing followed the delivery.
On 2026-09-26 the agent's ch12 sheet wrote fourteen thoughts as 内心独白, the parser kept only the mode it shares
with a voice off, and the binder copied only the mode - so every thought reached H3 as a voiceover over a face
on screen, whose lips H3 does not keep shut, while the book asked for thoughts to be mixed in afterwards.
Each unit test passed; the chapter was wrong.  So: one sheet with a line said, a thought and a voice off, and at
the end the question that matters - what does H3 hear, and what is laid over its take instead.
"""
from __future__ import annotations

import json
import re

import pytest

from novel_manga.application.packing.context import load_context
from novel_manga.application.packing.service import compile_plan
from novel_manga.application.rendering import h3
from novel_manga.models.bible import StoryBible
from novel_manga.planning.binding import merge
from novel_manga.planning.budget import configure_budget
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.validation import validate_and_normalize
from novel_manga.story.h3 import request_issues

SAID, THOUGHT, OFF = "终于……拿回来了。", "他们不会想到，背包里还有第二把钥匙。", "梁舟，别在那儿发呆了！"
SEGMENTS = [{"segment_id": "seg_1", "text": "梁舟把背包拖上溪岸，说终于拿回来了。"},
            {"segment_id": "seg_2", "text": "他想，他们不会想到背包里还有第二把钥匙。"},
            {"segment_id": "seg_3", "text": "远处老周喊他别在那儿发呆。"}]
BIBLE = {"novel_title": "测试", "genre": "generic", "visual_style": "统一3D动画", "palette": "自然色",
         "style_fingerprint": "fixture",
         "characters": [{"name": "梁舟", "appearance": "黑发青年", "wardrobe": "青衣"},
                        {"name": "老周", "appearance": "花白头发的老人", "wardrobe": "灰色短褂"}],
         "locations": ["溪岸：溪流与岩石"]}


def row(shot_id, sound, seconds):
    return {"镜号": shot_id, "摄影角度": "平视", "景别": "中景", "场景": "溪岸", "画面内容 / 动作": "梁舟坐在岸边查看背包。",
            "台词 / 声音": sound + "\n声音：水声", "机位 / 运镜 / 连续性": "固定机位", "叙事目的": "推进", "预算秒": seconds}


SHEETS = [row("A1", f"梁舟（说、松了口气）：“{SAID}”", 8),
          row("A2", f"梁舟（内心独白、压低）：“{THOUGHT}”", 10),
          row("A3", f"老周（画外音、提高嗓门）：“{OFF}”", 8)]
BINDING = {"video_title": "溪岸", "hook": "背包", "summary": "取包", "skipped_segments": [],
           "speaker_names": [{"written": "梁舟", "name": "梁舟"}, {"written": "老周", "name": "老周"}],
           "location_names": [{"written": "溪岸", "name": "溪岸"}],
           "bindings": [{"镜号": f"A{n}", "segment_id": f"seg_{n}", "source_quote": SEGMENTS[n - 1]["text"],
                         "start_state": "梁舟坐在岸边", "end_state": "梁舟坐在岸边", "light": "白天，斜射日光",
                         "in_frame": ["梁舟"], "extras": [], "actions": []} for n in (1, 2, 3)]}


@pytest.fixture
def traced(tmp_path, monkeypatch):
    merged = merge({"shots": SHEETS}, BINDING)
    ctx = PlannerContext.from_env()
    configure_budget(100, fast=False, ctx=ctx)
    ctx.spoken_range = (0, 300)
    ctx.authored_storyboard = True
    bible = StoryBible.model_validate(BIBLE)
    valid = validate_and_normalize(merged, SEGMENTS, bible, {"溪岸": bible.locations[0]},
                                   "\n".join(s["text"] for s in SEGMENTS), ctx=ctx)
    assert not valid.errors, valid.errors
    book = tmp_path / "trial"
    episode = book / "trial_1"
    episode.mkdir(parents=True)
    profile = {"style": "3d", "frame": "16:9", "inner_voice_delivery": "postmix"}
    (book / "story_bible.json").write_text(json.dumps(BIBLE, ensure_ascii=False), encoding="utf-8")
    (book / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    (episode / "segments.json").write_text(json.dumps(SEGMENTS, ensure_ascii=False), encoding="utf-8")
    script = {"profile": profile, "shots": valid.shots}
    (episode / "chapter_script.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    plan, _ = compile_plan(script, load_context(episode, book / "story_bible.json"))

    def translate(parts, schema, **kwargs):
        lines = [line for line in parts[0]["text"].splitlines() if line[:1].isdigit()]
        if schema.get("properties", {}).get("manners"):
            return {"manners": ["The line is delivered in a natural voice."] * len(lines)}
        return {"shots": [f"A young man sits on a stream bank, shot {i}." for i in range(1, len(lines) + 1)]}

    monkeypatch.setattr(h3, "ask_json", translate)
    for clip in plan["clips"]:
        assert h3.convert(clip, tries=1), clip["clip_id"]
    return valid.shots, plan


def clip_saying(plan, text):
    return next(c for c in plan["clips"] if text in json.dumps(c, ensure_ascii=False))


def spoken(english, words):
    """The sentence that hands H3 a line holding these words (punctuation inside <d> is normalised)."""
    match = re.search(rf"([^\n]*)<d>[^<]*{words}[^<]*</d>", english)
    return match.group(1) if match else None


def test_the_thought_is_still_a_thought_in_the_script(traced):
    shots, _ = traced
    thought = shots[1]["turns"][0]
    assert thought["delivery_mode"] == "offscreen_dialogue" and thought["inner_monologue"] is True
    assert not any(t.get("inner_monologue") for s in (shots[0], shots[2]) for t in s["turns"])


def test_the_thought_is_laid_over_a_silent_take_not_spoken_by_h3(traced):
    _, plan = traced
    clip = clip_saying(plan, THOUGHT)
    assert clip.get("audio_delivery") == "postmix" and clip["inner_voice"]["text"] == THOUGHT
    assert clip["inner_voice"]["speaker"] == "梁舟"
    assert "第二把钥匙" not in clip["prompt_h3"] and "<d>" not in clip["prompt_h3"]


def test_a_line_said_on_screen_is_spoken_by_h3(traced):
    _, plan = traced
    clip = clip_saying(plan, SAID)
    said = spoken(clip["prompt_h3"], "拿回来了")
    assert said is not None and " says" in said and "off-screen" not in said
    assert clip.get("audio_delivery") != "postmix"


def test_a_voice_off_stays_off_screen_and_is_not_mixed_in_later(traced):
    _, plan = traced
    clip = clip_saying(plan, OFF)
    said = spoken(clip["prompt_h3"], "发呆")
    assert said is not None and "off-screen" in said and clip.get("audio_delivery") != "postmix"


def test_no_request_contradicts_itself(traced):
    _, plan = traced
    assert {c["clip_id"]: request_issues(c) for c in plan["clips"] if request_issues(c)} == {}


def test_mixed_authored_shot_reaches_separate_native_and_postmixed_requests(tmp_path, monkeypatch):
    import sys
    import copy
    from novel_manga.application.planning.voice_splits import split_authored, PICTURE_FIELDS
    mixed = row('A1', f'梁舟（说）：“{SAID}”\n梁舟（内心独白）：“{THOUGHT}”', 18)
    def direct(prompt, schema):
        if 'problems' in schema['properties']:
            return {'problems': []}
        return {'shots': [{**{k: mixed[k] for k in PICTURE_FIELDS}, '预算秒': seconds,
                           '画面内容 / 动作': picture, '音效': '水声'} for seconds, picture in [
                             (8, '梁舟取出背包并开口说话。'),
                             (10, '背包已在岸上，梁舟低头思考，嘴唇自然闭合。')]]}
    directed, _ = split_authored({'shots': [mixed, SHEETS[2]]}, '\n'.join(s['text'] for s in SEGMENTS), ask=direct)
    binding = copy.deepcopy(BINDING)
    binding['bindings'][1]['镜号'] = 'A1.v2'
    monkeypatch.setattr(sys.modules[__name__], 'SHEETS', directed['shots'])
    monkeypatch.setattr(sys.modules[__name__], 'BINDING', binding)
    _, plan = traced.__wrapped__(tmp_path, monkeypatch)
    native, thought = clip_saying(plan, SAID), clip_saying(plan, THOUGHT)
    assert native['clip_id'] != thought['clip_id']
    assert spoken(native['prompt_h3'], '拿回来了')
    assert thought['audio_delivery'] == 'postmix'
    assert thought['inner_voice']['text'] == THOUGHT and '<d>' not in thought['prompt_h3']
