"""A stage split for length must not hand out the finale early.

split_long_shot's later parts used to open on the stage's final end_state - part 1 of 3 already
standing on the outcome before the lines that lead there were spoken - and every part carried the
final tableau as its own end_state, so a reaction that belongs to the last seconds was pinned on
the first.  Now: the later parts open on the action done and held, name the finale as what NOT to
show yet, and only the last part keeps the stage's end_state.
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


def test_part_two_opens_on_the_action_held_not_the_outcome():
    _, _, pieces = split_into(3)
    later = pieces[1]["visual_prompt"]
    assert "动作正在进行中" in later and "不重复该动作" in later
    assert "门开了" in later and "尚未发生" in later      # the finale named as pending, not painted


def test_only_the_last_part_keeps_the_final_end_state():
    _, _, pieces = split_into(3)
    assert pieces[-1]["end_state"] == "门开了，艾登愣在门口"
    assert "后续分段才成立" in pieces[0]["end_state"]      # part 1 ends mid-action
    assert "尚未发生" in pieces[1]["end_state"]            # middle parts pause


def test_no_part_repeats_the_action():
    _, compiler, pieces = split_into(3)
    for piece in pieces[1:]:
        assert "不重复" in piece["motion_prompt"]
    assert any(d["kind"] == "split_stage" for d in compiler.decisions)


def test_the_last_part_lands_the_result_instead_of_deferring_it():
    """The old contradiction: even the last part opened with 不提前出现最终结果 while its end_state
    restored it.  Now the last part says the action is past and IT presents the result."""
    _, _, pieces = split_into(3)
    last = pieces[-1]
    assert "已完成" in last["visual_prompt"] and "呈现最终结果" in last["visual_prompt"]
    assert "收尾" in last["motion_prompt"]


def test_a_departure_action_is_not_contradicted():
    """The review's case: the event is 托尼说完后飞出窗户, the end_state 诊室只剩席勒.  Part 1 must
    not claim the room is empty while its seconds still play; the last part must land it."""
    shot = {"index": 3, "origin_index": 3, "location": "诊室", "segment_id": "seg_01", "shot_scale": "中景",
            "visual_prompt": "托尼站在诊室窗边，席勒坐在桌后", "motion_prompt": "托尼说完后飞出窗户",
            "end_state": "诊室里只剩席勒",
            "turns": [{"delivery_mode": "visible_dialogue", "speaker_name": "托尼", "text": t}
                      for t in ["这地方我是一秒都待不下去了，连咖啡都是速溶的。" * 2, "回头见。" * 6]]}
    compiler = ClipCompiler(options())
    pieces = compiler.split_long_shot(shot)
    assert len(pieces) == 2, [p["split_part"] for p in pieces]
    assert "只剩席勒" in pieces[0]["end_state"] and "后续分段才成立" in pieces[0]["end_state"]
    assert "飞出窗户" in pieces[0]["end_state"]
    assert pieces[-1]["end_state"] == "诊室里只剩席勒"      # the last part lands it
    assert "已完成" in pieces[1]["visual_prompt"]           # and says the flight is past, not redone


def test_a_short_stage_is_touched_by_none_of_this():
    shot = stage(["一句话。"])
    assert ClipCompiler(options()).split_long_shot(shot) == [shot]
