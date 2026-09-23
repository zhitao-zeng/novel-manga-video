"""A stage may name its own place: 诊室 → 公交站 within one clip's stages becomes two requests.

flatten_clips used to stamp every stage with the clip header's location, so the packer never saw
a stage-level change of place: the structured field said one place while the stage's own text
painted another, and the request went out half-and-half.  Now the stage carries its location
through, and the packer's existing location cut - unchanged - turns the change of place into two
clips, joined later by the edit.
"""
from novel_manga.planning.validation import flatten_clips
from novel_manga.story.compilation import ClipCompiler, CompilerOptions
from novel_manga.application.profiles import frame_spec


def options(seconds=15):
    return CompilerOptions(seconds, seconds * .6, 3 if seconds <= 15 else 6, 'execution', 8,
                           frame=frame_spec({'frame': '16:9'}))


def stage(segment="s1", **kw):
    base = {"segment_id": segment, "source_quote": "…", "start_state": "诊室里", "event": "两人说话",
            "end_state": "话说完", "camera": "", "light": "", "sfx": "", "shot_scale": "中景",
            "turns": [{"speaker_name": "席勒", "delivery_mode": "visible_dialogue", "text": "……"}],
            "in_frame": ["席勒"], "actions": [], "extras": []}
    base.update(kw)
    return base


def test_a_stage_with_its_own_location_carries_it_through_flatten():
    raw = {"clips": [{"clip_id": "clip_01", "location": "诊室", "characters": ["席勒"], "avoid": "",
                      "stages": [stage(), stage(location="公交站", start_state="公交站牌下")]}]}
    shots = flatten_clips(raw)
    assert [s["location"] for s in shots] == ["诊室", "公交站"]


def test_a_stage_without_one_keeps_the_clips_location_as_always():
    raw = {"clips": [{"clip_id": "clip_01", "location": "诊室", "characters": ["席勒"], "avoid": "",
                      "stages": [stage(), stage()]}]}
    shots = flatten_clips(raw)
    assert [s["location"] for s in shots] == ["诊室", "诊室"]


def test_the_packer_cuts_the_change_of_place_into_two_requests():
    """The packer's own location rule - unchanged - now gets to see what the stages actually say."""
    raw = {"clips": [{"clip_id": "clip_01", "location": "诊室", "characters": ["席勒"], "avoid": "",
                      "stages": [stage(), stage(location="公交站", start_state="公交站牌下"),
                                 stage(location="公交站", segment="s2")]}]}
    shots = flatten_clips(raw)
    compiler = ClipCompiler(options())
    clips = compiler.pack(shots)
    video = [c for c in clips if c["kind"] == "video"]
    assert [c["location"] for c in video] == ["诊室", "公交站"]
    assert [len(c["shots"]) for c in video] == [1, 2]
    cut = next(d for d in compiler.decisions if d["kind"] == "cut")
    assert cut["decision_reason"] == "location"


def test_the_readiness_check_sees_the_stage_level_place_now():
    """location_issues already compared a clip's place with its source stages'; with the stage
    field real, a mismatch is caught instead of both sides agreeing on the header's word."""
    from novel_manga.application.preparation.readiness import location_issues
    clip = {"location": "公交站", "shot_indexes": [1, 2], "references": [
        {"role": "location", "name": "公交站"}]}
    shots = {1: {"location": "诊室"}, 2: {"location": "公交站"}}
    assert location_issues(clip, shots) == ["location: source stages cross locations; recut required"]
    shots_agree = {1: {"location": "公交站"}, 2: {"location": "公交站"}}
    assert location_issues(clip, shots_agree) == []
