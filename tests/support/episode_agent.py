"""Small authored-episode fixtures shared by contract and integration tests."""

CATALOG = [
    {"id": "b", "role": "character", "name": "乙", "path": "cards/b.jpeg"},
    {"id": "room", "role": "location", "name": "房间", "path": "cards/room.jpeg"},
    {"id": "a", "role": "character", "name": "甲", "path": "cards/a.jpeg"},
    {"id": "a_voice", "role": "voice", "name": "甲", "path": "voices/a.wav"},
]
SOURCES = [{"segment_id": "s1", "text": "原文：甲走进房间。\n甲说：你来啦。"},
           {"segment_id": "s2", "text": "原文：乙看着桌边的野山羊。"}]


def shot(index, *, turns=None):
    return {"index": index, "segment_ids": ["s1", "s2"], "characters": ["甲", "乙"],
            "in_frame": ["甲"], "location": "房间", "start_state": "甲坐在桌边。",
            "event": "甲抬眼。", "end_state": "甲看向门口。", "camera": "手部近景，平视固定。",
            "turns": [{"speaker_name": "甲", "text": "你来啦。", "delivery_mode": "visible_dialogue"}]
                     if turns is None else turns}


def prompt(shots, *, silent=False, voice=True):
    definitions = ("<Subject 1> is the person shown in <Picture 1>.\n"
                   "<Subject 2> is the person shown in <Picture 3>.\n"
                   "<Subject 3> is the setting shown in <Picture 2>.")
    if voice and not silent:
        definitions += "\n<Audio 1> is the voice-timbre reference for <Subject 2> (S1)."
    body, speakers, elapsed, heard = [], {}, 0, set()
    for stage, row in enumerate(shots, 1):
        duration = row.get("duration_seconds", 15)
        cut = f"At 00:{elapsed:06.3f}, " if stage > 1 else ""
        body.append(f"[Shot {stage}] {cut}Planned duration: {duration:g} seconds. "
                    "A close view of the hands shows a relaxed grip on the cup.")
        for turn in ([] if silent else row["turns"]):
            owner = turn["speaker_name"]
            speakers.setdefault(owner, len(speakers)+1)
            subject = {"甲": "<Subject 2>", "乙": "<Subject 1>"}.get(owner, "An off-screen voice")
            delivery = " in an off-screen voiceover" if turn["delivery_mode"] == "offscreen_dialogue" else ""
            text = f"{subject} (S{speakers[owner]}) says{delivery} <d>[Chinese] {turn['text']}</d>"
            if voice and owner == "甲" and owner not in heard:
                text += " The speaking voice follows the timbre referenced from <Audio 1>."
            body.append(text)
            heard.add(owner)
        elapsed += duration
    return (f"subject_definitions:\n{definitions}\n\nsummary:\n[reference generation] A quiet meeting.\n\n"
            "retention_analysis:\n<Subject 1>: fully_preserved - identity.\n"
            "<Subject 2>: partially_preserved - authored physical state and identity.\n"
            "<Subject 3>: fully_preserved - architecture.\n"
            + ("<Audio 1>: reference - voice timbre.\n" if voice and not silent else "")
            + "\ndetailed_description:\n" + "\n".join(body)
            + "\noverall_soundscape:\nA steady breeze.\n\nnon_diegetic_music:\nNone.")


def episode(*, turns=None, silent=False, voice=True):
    shots = [shot(i, turns=turns) for i in range(1, 9)]
    clips = [{"shot_indexes": [row["index"]], "request_seconds": 15,
              "reference_ids": ["b", "room", "a"] + (["a_voice"] if voice else []),
              "prompt_h3": prompt([row], silent=silent, voice=voice)} for row in shots]
    return {"title": "新创作", "adaptation_notes": "独立压缩。",
            "chapter_script": {"shots": shots}, "clips": clips}
