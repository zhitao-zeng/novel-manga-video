"""`<Audio N>` names the speaker id, not only the subject.

The guide: "When an <Audio N> explicitly corresponds to a target speaker, reuse that speaker's
global ID in the definition... <Audio 1> is the voice-timbre reference for <Subject 1> (S1)."
We wrote the line without the (Sx), leaving H3 to pair a voice with a speaker by position - the
one binding the H3 community reports as fixing two-speaker timbre swapping.  The ids are assigned
by first vocal event, so a clip where the second subject opens the mouth first binds its audio to
(S1), and the subject numbering is untouched.
"""
from __future__ import annotations

import re

from novel_manga.story.h3 import compose


def clip(references, lines):
    return {"clip_id": "clip_01", "request_seconds": 10, "cast": ["席勒", "托尼·斯塔克"],
            "references": references, "dialogue_bindings": lines}


CARDS = [{"role": "character", "name": "席勒", "asset_id": "c1", "path": "a/turnaround.jpeg"},
         {"role": "character", "name": "托尼·斯塔克", "asset_id": "c2", "path": "b/turnaround.jpeg"}]
VOICES = [{"role": "voice", "name": "席勒", "path": "series_assets/voices/席勒.wav"},
          {"role": "voice", "name": "托尼·斯塔克", "path": "series_assets/voices/托尼·斯塔克.wav"}]


def turn(stage, who, text):
    return {"stage": stage, "speaker_name": who, "text": text, "delivery_mode": "visible_dialogue", "emotion": ""}


def test_the_audio_line_carries_the_speaker_id_the_body_assigned():
    prompt = compose(clip(CARDS + VOICES, [turn(1, "席勒", "一")]), ["A room."], [("", [])])
    assert "<Audio 1> is the voice-timbre reference for <Subject 1> (S1)." in prompt


def test_the_id_follows_who_speaks_first_not_the_card_order():
    # Stark holds card 2 but opens his mouth first, so his voice reference is (S1).
    prompt = compose(clip(CARDS + VOICES, [turn(1, "托尼·斯塔克", "一"), turn(1, "席勒", "二")]),
                     ["A room."], [("", [])])
    assert "<Audio 2> is the voice-timbre reference for <Subject 2> (S1)." in prompt
    assert "<Audio 1> is the voice-timbre reference for <Subject 1> (S2)." in prompt


def test_a_voice_whose_owner_never_speaks_here_gets_no_invented_id():
    prompt = compose(clip(CARDS + VOICES, [turn(1, "席勒", "一")]), ["A room."], [("", [])])
    assert "<Audio 2> is the voice-timbre reference for <Subject 2>." in prompt


def test_the_task_prefix_announces_the_audio_reference_and_the_ids_stay_out_of_retention():
    prompt = compose(clip(CARDS + VOICES, [turn(1, "席勒", "一")]), ["A room."], [("", [])])
    assert "[reference generation + audio reference]" in prompt
    retention = prompt.split("retention_analysis:\n", 1)[1].split("\n\ndetailed_description", 1)[0]
    assert not re.search(r"\(S\d+\)", retention)


def test_a_clip_without_voices_declares_no_audio_at_all():
    prompt = compose(clip(CARDS, [turn(1, "席勒", "一")]), ["A room."], [("", [])])
    assert "<Audio" not in prompt and "[reference generation]" in prompt
