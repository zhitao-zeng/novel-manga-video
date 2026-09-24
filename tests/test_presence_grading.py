"""Presence is judged, not scanned: the judge's per-chapter pass over the cast.

The original defect is not a field-name problem: the planner wrote 席勒继续说话，提到霍华德和佩珀
into the event line, every field rule saw the names and seated 佩珀 in the clinic.  grade_presence
gives each candidate to a reader, then corrects BOTH ways - a talked-about name comes out of the
cast (kept as mentioned_only), an on_camera one goes in - while visible speakers and action
partners stay whatever the judge says: their fields are facts, not readings.
"""
from novel_manga.application.planning import presence
from novel_manga.planning import cast as pc_cast
from novel_manga.planning.context import PlannerContext

NAMES = ["席勒", "托尼·斯塔克", "佩珀", "霍华德", "贾维斯"]


def indexed_ctx():
    ctx = PlannerContext()
    for name in NAMES:
        ctx.entity_forms[name] = {name}
        ctx.entity_generic[name] = False
    return ctx


CTX = indexed_ctx()


def speaks(name, text):
    return {"speaker_name": name, "delivery_mode": "visible_dialogue", "text": text}


def judge_answer(rows):
    return lambda parts, schema, **kw: {"grades": rows}


def test_the_original_case_a_mention_in_the_event_line_is_not_presence(monkeypatch):
    """席勒继续说话，提到霍华德和佩珀 - the exact bad case: names in motion_prompt, judged talked_about,
    must not seat them.  The old field rule graded this on_camera."""
    shot = {"characters": ["席勒"], "motion_prompt": "席勒继续说话，提到霍华德和佩珀。",
            "visual_prompt": "席勒站在诊室里", "end_state": "", "turns": [],
            "actions": [{"actor": "席勒", "action": "说话", "target": ""}]}
    monkeypatch.setattr(presence, "ask_json", judge_answer(
        [{"shot": 1, "name": "霍华德", "grade": "talked_about"},
         {"shot": 1, "name": "佩珀", "grade": "talked_about"}]))
    grades = presence.grade_presence([shot], NAMES, ctx=CTX)
    assert grades[1] == {"霍华德": "talked_about", "佩珀": "talked_about"}


def test_correct_both_ways_promote_and_demote(monkeypatch):
    """The chapter script flow's correction, judged grades applied to a cast that was wrong twice."""
    shots = [
        # 佩珀 in the cast but only talked about; 托尼 missing but standing in the picture
        {"characters": ["席勒", "佩珀"], "motion_prompt": "席勒提到佩珀，托尼·斯塔克站在窗边",
         "visual_prompt": "诊室内", "end_state": "", "turns": [speaks("席勒", "佩珀会不高兴的。")],
         "actions": [{"actor": "席勒", "action": "说话", "target": ""}]},
    ]
    monkeypatch.setattr(presence, "ask_json", judge_answer(
        [{"shot": 1, "name": "佩珀", "grade": "talked_about"},
         {"shot": 1, "name": "托尼·斯塔克", "grade": "on_camera"}]))
    grades = presence.grade_presence(shots, NAMES, ctx=CTX)
    # apply what flow.py applies
    from novel_manga.application.planning.presence import structural_on_camera
    shot = shots[0]
    judged, structural = grades.get(1) or {}, structural_on_camera(shot)
    cast = list(shot["characters"])
    for name, grade in judged.items():
        if grade == "on_camera" and name in NAMES and name not in cast:
            cast.append(name)
    for name in list(cast):
        if judged.get(name) == "talked_about" and name not in structural:
            cast.remove(name)
            shot.setdefault("mentioned_only", []).append(name)
    assert cast == ["席勒", "托尼·斯塔克"]
    assert shot["mentioned_only"] == ["佩珀"]


def test_a_visible_speaker_is_structural_and_never_graded():
    """Facts over readings: the speaker's own turn and an action's partners are on camera
    whatever the judge says - the 761 line, held by structure rather than by a prompt."""
    shot = {"characters": ["席勒"], "motion_prompt": "薇奥拉环住莱恩", "visual_prompt": "", "end_state": "",
            "turns": [speaks("薇奥拉公主", "……")],
            "actions": [{"actor": "薇奥拉公主", "action": "环住", "target": "莱恩·格雷"}]}
    keep = presence.structural_on_camera(shot)
    assert "薇奥拉公主" in keep and "莱恩·格雷" in keep


def test_a_judge_outage_returns_no_grades(monkeypatch):
    def boom(parts, schema, **kw):
        raise RuntimeError("endpoint down")
    monkeypatch.setattr(presence, "ask_json", boom)
    shot = {"characters": ["席勒"], "motion_prompt": "席勒提到佩珀", "visual_prompt": "", "end_state": "", "turns": []}
    assert presence.grade_presence([shot], NAMES, ctx=CTX) == {}


def test_no_candidates_no_call(monkeypatch):
    monkeypatch.setattr(presence, "ask_json",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("不该有调用")))
    shot = {"characters": ["席勒"], "motion_prompt": "席勒喝咖啡", "visual_prompt": "", "end_state": "", "turns": []}
    assert presence.grade_presence([shot], ["席勒"], ctx=CTX) == {}


def test_garbled_judge_rows_are_dropped_not_guessed(monkeypatch):
    monkeypatch.setattr(presence, "ask_json", judge_answer([
        {"shot": 99, "name": "佩珀", "grade": "on_camera"},       # out of range
        {"shot": 1, "name": "不存在的人", "grade": "on_camera"},   # not in the book
        {"shot": 1, "name": "佩珀", "grade": "maybe"},             # not a grade
        {"shot": 1, "name": "佩珀", "grade": "talked_about"},      # good row survives
    ]))
    shot = {"characters": ["席勒"], "motion_prompt": "席勒提到佩珀", "visual_prompt": "", "end_state": "",
            "turns": [speaks("席勒", "佩珀说的对。")]}
    assert presence.grade_presence([shot], NAMES, ctx=CTX) == {1: {"佩珀": "talked_about"}}


def test_the_judge_gets_the_roster_so_it_knows_who_has_no_body(monkeypatch):
    """ch12 round four: the event said 斯塔克强调贾维斯死机, the judge graded 贾维斯 on_camera
    because a name cannot say who is bodiless.  The roster must reach the judge's prompt."""
    seen = {}

    def fake(parts, schema, **kw):
        seen["prompt"] = parts[0]["text"]
        return {"grades": [{"shot": 1, "name": "贾维斯", "grade": "talked_about"}]}

    monkeypatch.setattr(presence, "ask_json", fake)
    shot = {"characters": ["托尼·斯塔克"], "motion_prompt": "斯塔克强调贾维斯死机，要求席勒负责。",
            "visual_prompt": "诊室", "end_state": "", "turns": [speaks("托尼·斯塔克", "贾维斯死机了。")]}
    grades = presence.grade_presence([shot], NAMES, ctx=CTX,
                                     roster={"贾维斯": "无实体，以全息投影或界面形式出现"})
    assert "贾维斯" in seen["prompt"] and "无实体" in seen["prompt"]
    assert grades == {1: {"贾维斯": "talked_about"}}


def test_the_fallback_talk_verbs_do_not_seat_a_bodiless_name():
    """强调/说明/解释 are talk-about verbs: with the judge down, the rules alone must keep
    贾维斯 off camera when the event only says 斯塔克强调贾维斯死机."""
    shot = {"characters": ["托尼·斯塔克"], "motion_prompt": "斯塔克强调贾维斯死机，要求席勒负责。",
            "visual_prompt": "诊室", "end_state": "", "turns": [],
            "actions": [{"actor": "托尼·斯塔克", "action": "强调", "target": ""}]}
    cast, added = pc_cast.complete_characters(["托尼·斯塔克"], shot, NAMES, ctx=CTX)
    assert added == [] and "贾维斯" not in cast
    candidates = pc_cast.presence_candidates(["托尼·斯塔克"], shot, NAMES, ctx=CTX)
    assert all(e["grade"] == "talked_about" for e in candidates["贾维斯"])
