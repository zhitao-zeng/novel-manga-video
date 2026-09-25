"""A stage split for length plays its outcome once, at the end, and nothing is written for the parts.

split_long_shot's later parts once opened on the stage's final end_state - part 1 of 3 already standing
on the outcome before the lines that lead there were spoken.  Prose then patched that over (承接上一段：
……正在进行中；最终结果尚未发生) and replaced the picture the writer wrote; ch12's visor drifted across the
parts (2026-09-25).  Now every part opens on the writer's own picture, the parts before the last hold it
and only speak, and the last part plays the stage's event and lands its end_state.
"""
from novel_manga.application.profiles import frame_spec
from novel_manga.story.compilation import ClipCompiler, CompilerOptions


def options(seconds=15):
    return CompilerOptions(seconds, seconds * .6, 3, 'execution', 8, frame=frame_spec({'frame': '16:9'}))


def stage(turns):
    return {"index": 9, "origin_index": 9, "location": "大门前", "segment_id": "seg_01", "shot_scale": "中景",
            "visual_prompt": "艾登站在紧闭的大门前", "motion_prompt": "艾登用力推门",
            "end_state": "门开了，艾登愣在门口",
            "turns": [{"delivery_mode": "visible_dialogue", "speaker_name": "艾登", "text": text} for text in turns]}


def split_into(parts_count=2):
    # one line per part: long enough to cross the clip cap together, short enough to stay whole
    line = "这门怎么这么重啊，怎么推都推不动。" * 2
    shot = stage([line] * parts_count)
    compiler = ClipCompiler(options())
    pieces = compiler.split_long_shot(shot)
    assert len(pieces) == parts_count, [p["split_part"] for p in pieces]
    return shot, compiler, pieces


def test_the_parts_before_the_last_hold_the_opening_picture():
    _, _, pieces = split_into(3)
    for piece in pieces[:-1]:
        assert piece["visual_prompt"] == "艾登站在紧闭的大门前" and piece["end_state"] == ""
        assert piece["motion_prompt"] == "" and piece["actions"] == []


def test_only_the_last_part_plays_the_action_and_lands_the_end_state():
    _, compiler, pieces = split_into(3)
    assert [p["motion_prompt"] for p in pieces] == ["", "", "艾登用力推门"]
    assert pieces[-1]["visual_prompt"] == "艾登站在紧闭的大门前"
    assert pieces[-1]["end_state"] == "门开了，艾登愣在门口"
    assert any(d["kind"] == "split_stage" for d in compiler.decisions)


def test_nothing_is_written_for_the_parts():
    _, _, pieces = split_into(3)
    written = " ".join(str(p[k]) for p in pieces for k in ("visual_prompt", "motion_prompt", "end_state"))
    for invented in ("承接上一段", "正在进行中", "尚未发生", "后续分段", "台词已说完", "收尾"):
        assert invented not in written


def test_a_departure_action_is_not_contradicted():
    """The review's case: the event is 托尼说完后飞出窗户, the end_state 诊室只剩席勒.  The room must not
    be empty while his lines still play: the flight and the empty room belong to the last part."""
    shot = {"index": 3, "origin_index": 3, "location": "诊室", "segment_id": "seg_01", "shot_scale": "中景",
            "visual_prompt": "托尼站在诊室窗边，席勒坐在桌后", "motion_prompt": "托尼说完后飞出窗户",
            "end_state": "诊室里只剩席勒",
            "turns": [{"delivery_mode": "visible_dialogue", "speaker_name": "托尼", "text": t}
                      for t in ["这地方我是一秒都待不下去了，连咖啡都是速溶的。" * 2, "回头见。" * 6]]}
    pieces = ClipCompiler(options()).split_long_shot(shot)
    assert len(pieces) == 2, [p["split_part"] for p in pieces]
    assert (pieces[0]["motion_prompt"], pieces[0]["end_state"]) == ("", "")
    assert (pieces[-1]["motion_prompt"], pieces[-1]["end_state"]) == ("托尼说完后飞出窗户", "诊室里只剩席勒")
