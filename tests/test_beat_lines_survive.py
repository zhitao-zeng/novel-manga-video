"""A beat's line must be SPOKEN somewhere in the packed plan - described is not delivered.

The review's case: the blueprint's beat carried 托尼's answer to how they travel, the plan cited
the segment, and the answer ended up only in the stage's event field - the audience never hears
it, and the scene's exit question is left hanging.  beat_lines_survive checks every beat's
source_quote against the packed turns; a picture-only survival is exactly what it reports.
"""
from novel_manga.planning.validation import beat_lines_survive, _line_carries
from novel_manga.planning.issues import PlanningCode


def beat(quote, bid="beat_1"):
    return {"beat_id": bid, "segment_id": "s1", "source_quote": quote}


def shot_turns(*turns, picture=""):
    return {"turns": list(turns), "visual_prompt": picture, "motion_prompt": "", "end_state": ""}


def speaks(name, text):
    return {"speaker_name": name, "delivery_mode": "visible_dialogue", "text": text}


def test_a_beat_whose_line_is_spoken_survives():
    errors = []
    beat_lines_survive({"beats": [beat("不然呢？你打算怎么过去？")]},
                       [shot_turns(speaks("托尼·斯塔克", "不然呢？你打算怎么过去？"))], errors)
    assert errors == []


def test_a_compressed_paraphrase_still_carries_the_beat():
    errors = []
    beat_lines_survive({"beats": [beat("你把他弄坏的就得修好，不然我让佩珀炒了你。")]},
                       [shot_turns(speaks("托尼·斯塔克", "你弄坏的就得修好，不然我让佩珀炒了你"))], errors)
    assert errors == []


def test_a_line_that_only_lives_in_the_picture_text_is_reported():
    """The one the review named: the exit answer described in an event field, never spoken."""
    errors = []
    beat_lines_survive({"beats": [beat("不然呢？你打算怎么过去？")]},
                       [shot_turns(speaks("席勒", "你该不会想让我坐这个过去吧？"),
                                   picture="斯塔克反问席勒打算怎么过去，两人对视")], errors)
    assert len(errors) == 1 and errors[0].code == PlanningCode.BEAT_LINE_LOST
    assert "没有任何台词承载" in errors[0].message


def test_the_cut_point_question_and_answer_pair_both_survive():
    """The scene's hand-off: the question in one beat, the answer in the next - both spoken."""
    errors = []
    beat_lines_survive(
        {"beats": [beat("你该不会想让我坐这个过去吧？", "beat_1"),
                   beat("不然呢？你打算怎么过去？", "beat_2")]},
        [shot_turns(speaks("席勒", "你该不会想让我坐这个过去吧？")),
         shot_turns(speaks("托尼·斯塔克", "不然呢？你打算怎么过去？"))], errors)
    assert errors == []


def test_a_visual_beat_with_a_short_quote_is_left_alone():
    """Too short to identify, or the beat's business is visual: not this check's case."""
    errors = []
    beat_lines_survive({"beats": [beat("他点头。")]},
                       [shot_turns(picture="他点了点头")], errors)
    assert errors == []


def test_line_carries_head_run_and_share():
    from novel_manga.planning import text as pc_text
    assert _line_carries("不然呢你打算怎么过去", "不然呢你打算怎么过去")
    spoken = pc_text.quote_key("aa" + "不然呢你打算" + "bb")
    assert _line_carries(spoken, "不然呢？你打算怎么过去？")            # head run inside a longer turn
    assert not _line_carries("完全无关的台词内容", "不然呢你打算怎么过去")
