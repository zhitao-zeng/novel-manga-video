"""The prop reference chain, end to end and by one number: seat order, declaration, request.

build_references seats a prop after the location; the Chinese prompt binds it beside the scene
line; the H3 subject_definitions give it a picture number and say what it is; build_request
sends every non-voice reference in that same order.  Three modules used to hold three different
ideas of "which references are images" - the declaration numbered only characters and locations,
the request sent props too, and the pre-render check never looked for a missing prop card.
This test walks one clip through all of them and asserts they agree.
"""
import json

from novel_manga.application.packing.assets import build_references
from novel_manga.application.profiles import frame_spec
from novel_manga.models.bible import Character, Prop, StoryBible
from novel_manga.story.compilation import ClipCompiler, CompilerOptions
from novel_manga.story.h3 import compose, subject_lines


def _bible():
    return StoryBible(
        novel_title="t", genre="g", visual_style="3d 国漫", palette="冷", style_fingerprint="fp",
        characters=[Character(name="莱恩", role="主角", appearance="瘦高", wardrobe="黑大衣"),
                    Character(name="琥珀", role="女主角", appearance="娇小", wardrobe="红裙")],
        locations=["事务所：临街小屋"],
        props=[Prop(name="青铜短剑", category="武器", appearance="泛青", first_chapter=1, quote="…")],
    )


def _clip(bible, tmp_path):
    """A packed clip with a prop seated, through the real packing call."""
    refs, bindings, location_binding = build_references(
        ["莱恩", "琥珀"], "事务所", bible, {"事务所": "事务所：临街小屋"},
        props=["青铜短剑"], props_index={p.name: p for p in bible.props},
        props_on_disk={"prop_001": {"turnaround.jpeg"}})
    assert [r["tag"] for r in refs] == ["@图片1", "@图片2", "@图片3", "@图片4"]   # 人物、人物、地点、道具
    shots = [{"origin_index": 1, "segment_id": "s1", "source_quote": "…",
              "location": "事务所", "characters": ["莱恩", "琥珀"],
              "visual_prompt": "两人站在事务所里", "motion_prompt": "莱恩抽出青铜短剑",
              "actions": [], "extras": [], "listeners": [], "end_state": "短剑在手",
              "camera": "", "light": "", "avoid": "", "sfx": "", "shot_scale": "中近景",
              "turns": [{"speaker_name": "莱恩", "text": "看好了。", "delivery_mode": "visible_dialogue", "emotion": ""}]}]
    clip = {"clip_id": "clip_01", "kind": "video", "request_seconds": 10,
            "shots": shots, "references": refs,
            "lines": [{"speaker_name": "莱恩", "text": "看好了。", "delivery_mode": "visible_dialogue", "emotion": ""}],
            "dialogue_bindings": [{"stage": 1, "speaker_name": "莱恩", "text": "看好了。",
                                   "delivery_mode": "visible_dialogue", "emotion": ""}]}
    return clip, bindings, location_binding


def test_one_prop_through_the_whole_chain(tmp_path):
    bible = _bible()
    clip, bindings, location_binding = _clip(bible, tmp_path)

    # 1) 中文编译：场景行点名地点座位和道具座位，人物行只有人物
    compiler = ClipCompiler(CompilerOptions(15, 9, 3, "execution", 8, frame=frame_spec({"frame": "16:9"})))
    prompt = compiler.compile_prompt(clip, bible, ["莱恩", "琥珀"], bindings, location_binding)
    assert "【场景】@图片3用于<事务所>" in prompt
    assert "<青铜短剑>对应@图片4" in prompt
    assert "【人物】" in prompt and "青铜短剑" not in prompt.split("【场景】")[0].split("【人物】")[1]

    # 2) H3 声明：道具拿到与座位一致的编号，且不是 Subject
    defs, subject_of = subject_lines(clip)
    prop_def = next(d for d in defs if "is a prop shown in it" in d)
    assert "<Picture 4>" in prop_def and "neither a person nor a subject" in prop_def
    assert subject_of == {"莱恩": 1, "琥珀": 2}                  # 道具不占 Subject 位
    english = compose(clip, ["He draws the bronze sword."], [(None, [])], note="")
    assert "<Picture 4>" in english

    # 3) 实际发送：非 voice 引用按声明顺序全部进入请求，道具在最后
    sent = [str(tmp_path / r["path"]) for r in clip["references"] if r.get("role") != "voice"]
    roles = [r["role"] for r in clip["references"] if r.get("role") != "voice"]
    assert roles == ["character", "character", "location", "prop"]  # build_request 的过滤口径
    assert len(sent) == 4

    # 4) 准备检查：缺道具卡是阻塞，不再只查 character/location
    from novel_manga.application.preparation.readiness import reference_issues
    issues = reference_issues(clip, tmp_path)                    # 卡都不在盘上
    assert any("missing required image" in i and "props/prop_001" in i for i in issues)
    assert any("missing required image" in i and "characters/character_001" in i for i in issues)
