"""The storyboard must not tell the person who is speaking to keep their mouth shut.

blocking_note gave the foreground to the first physical action's actor and sent everyone else to
"只在后景侧身或背对镜头，不开口".  It never asked who speaks.  Schiller talks while Stark recoils,
so Stark took the frame and Schiller - mid-sentence - was told 不开口: 15 of chapter 12's 43 spoken
stages, and the renderer animated the mouth it could see.  Separately, a listener kept on camera as
a back view was dropped from the cast, losing the reference picture that says whose back it is.
"""
from __future__ import annotations

from novel_manga.application.packing.context import compiler_options
from novel_manga.story.compilation import ClipCompiler
from novel_manga.story.framing import blocking_note


def shot(characters, actions, turns=(), **extra):
    return {"characters": list(characters), "actions": list(actions), "turns": list(turns), **extra}


def speaks(name):
    return {"delivery_mode": "visible_dialogue", "speaker_name": name, "text": "……"}


def test_the_speaker_takes_the_foreground_from_the_one_who_merely_moves():
    note = blocking_note(shot(["席勒", "托尼·斯塔克"],
                              [{"actor": "托尼·斯塔克", "target": None}],
                              [speaks("席勒")]))
    assert "席勒在前景居中" in note
    assert "不开口" not in note                      # the one who moves stays visible; nobody is silenced
    assert "托尼·斯塔克只在后景" not in note


def test_nobody_who_speaks_is_sent_to_the_silent_back_row():
    note = blocking_note(shot(["席勒", "托尼·斯塔克", "佩珀"],
                              [{"actor": "托尼·斯塔克", "target": None}],
                              [speaks("席勒"), speaks("托尼·斯塔克")]))
    assert "佩珀只在后景侧身或背对镜头，不开口" in note
    for speaker in ("席勒", "托尼·斯塔克"):
        assert f"{speaker}只在后景" not in note


def test_a_prop_that_moves_does_not_take_the_frame_from_the_person_speaking():
    # ch12 clip_11: the mech was the actor of the stage's only action, so it held the centre and
    # Stark, speaking, was put behind it.
    note = blocking_note(shot(["托尼·斯塔克"], [{"actor": "银白色机甲", "target": None}],
                              [speaks("托尼·斯塔克")], extras=["银白色机甲"]))
    assert "托尼·斯塔克在前景居中" in note
    assert "托尼·斯塔克只在后景" not in note


def test_a_silent_stage_still_frames_whoever_acts():
    note = blocking_note(shot(["席勒", "托尼·斯塔克"], [{"actor": "托尼·斯塔克", "target": "席勒"}]))
    assert "托尼·斯塔克在画面左侧前景，席勒在右侧前景" in note


def test_a_listener_keeps_the_card_that_says_whose_back_it_is():
    clip = {"shots": [shot(["席勒"], [], [speaks("席勒")], listeners=["托尼·斯塔克"])]}
    assert ClipCompiler(compiler_options()).clip_cast(clip) == ["席勒", "托尼·斯塔克"]


def test_every_listener_keeps_a_card_however_many_there_are():
    """This asserted the opposite this morning, when a listener was a bystander the crowding rule
    could drop.  The picture-text scan (2026-09-22) made that rule fire on people the shot plainly
    stands in frame, so the rule now applies only to someone the shot never puts on camera.  What
    still bounds the count is complete_characters' cap of six additions."""
    clip = {"shots": [shot(["席勒"], [], [speaks("席勒")], listeners=["托尼·斯塔克", "佩珀", "哈皮"])]}
    assert ClipCompiler(compiler_options()).clip_cast(clip) == ["席勒", "托尼·斯塔克", "佩珀", "哈皮"]


def test_a_listener_is_not_a_bystander_the_crowding_rule_can_drop():
    """The rule that keeps a busy clip from blending two similar faces counted listeners as
    droppable bystanders.  With the picture-text scan adding a third name (2026-09-22), that took
    托尼·斯塔克's card away in shots he is plainly standing in - and his back rendered as a
    stranger's.  A back is not a face; only someone the shot never puts on camera is a bystander."""
    clip = {"shots": [shot(["席勒"], [], [speaks("席勒")], listeners=["托尼·斯塔克", "贾维斯"])]}
    assert ClipCompiler(compiler_options()).clip_cast(clip) == ["席勒", "托尼·斯塔克", "贾维斯"]


def test_someone_the_shot_never_puts_on_camera_is_still_dropped_when_it_is_crowded():
    clip = {"shots": [shot(["席勒", "托尼·斯塔克", "佩珀"], [], [speaks("席勒")],
                           visual_prompt="席勒和托尼·斯塔克对坐")]}
    assert ClipCompiler(compiler_options()).clip_cast(clip) == ["席勒", "托尼·斯塔克"]
