"""Grade presence on the judge's channel: one call per chapter, after validation.

The rule-based scan cannot tell a mention from a body.  "席勒提到霍华德和佩珀" in an event line
grades on_camera by every field-name rule - the name is there, the field paints the picture - and
佩珀 got a seat in the clinic.  The opposite error is 761: the model's own in_frame omitted 薇奥拉
while its description had her kissing 莊恩, and the cat did the kissing.  Field names cannot split
"talks about" from "stands there"; a reader can, and the judge is a reader.

The same shape as prop_marks: the scan only FINDS candidates (whose name appears where), the judge
grades them (on_camera / talked_about / absent), and structural facts then overrule the judge in
both directions - a visible speaker or an action's actor/target is on camera whatever the judge
says, because their own fields say so; and a judge outage falls back to the field-grade rules,
which are what ran before this module existed.
"""
from __future__ import annotations

from novel_manga.llm.client import ask_json
from novel_manga.planning import cast as pc_cast
from novel_manga.planning.context import PlannerContext

_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["grades"],
           "properties": {"grades": {"type": "array", "items": {"type": "object",
                                    "additionalProperties": False,
                                    "required": ["shot", "name", "grade"],
                                    "properties": {"shot": {"type": "integer"},
                                                   "name": {"type": "string"},
                                                   "grade": {"type": "string",
                                                             "enum": ["on_camera", "talked_about", "absent"]}}}}}}


def structural_on_camera(shot: dict) -> set[str]:
    """Who the shot's own structure puts in the picture, whatever any judge says.

    A visible speaker is on camera - the renderer animates their mouth.  An action's actor and
    target are in the picture (fields.py asks in_frame to hold exactly them).  These are facts of
    the shot's fields, not readings of its prose; grading them is not the judge's to do.
    """
    keep = {str(turn.get("speaker_name") or "") for turn in (shot.get("turns") or [])
            if turn.get("delivery_mode") == "visible_dialogue" and turn.get("speaker_name")}
    for action in shot.get("actions") or []:
        for who in (action.get("actor"), action.get("target")):
            if who:
                keep.add(str(who))
    return keep


def grade_presence(shots: list[dict], everyone: list[str], *, ctx: PlannerContext = None,
                   settings=None, log=None) -> dict[int, dict[str, str]]:
    """{shot position: {name: grade}} - the judged grade of every candidate the scan found.

    Returns {} when there is nothing to grade or the judge cannot be reached: the caller then
    keeps the rule-based grades.  Candidates only: names the scan found in the shot's own fields
    or lines, excluding the cast (who are in by declaration) and the structurally-on-camera (who
    are in by fact).
    """
    ctx = ctx or PlannerContext()
    everyone = list(everyone)
    rows = []          # (position, name, where it was found) - the judge sees why it is a candidate
    for position, shot in enumerate(shots, start=1):
        cast = set(shot.get("characters") or [])
        structural = structural_on_camera(shot)
        candidates = pc_cast.presence_candidates(sorted(cast), shot, everyone, ctx=ctx)
        candidates.update({name: [{"field": "cast", "grade": "on_camera"}] for name in cast - structural})
        for name in sorted(candidates):
            if name in structural:
                continue                       # a fact; not the judge's to grade
            where = sorted({str(e.get("field")) for e in candidates[name]})
            rows.append((position, name, where))
    if not rows:
        return {}
    lines = []
    for position, shot in enumerate(shots, start=1):
        text = "；".join(p for p in (str(shot.get("motion_prompt") or "").strip(),
                                     str(shot.get("visual_prompt") or "").strip(),
                                     str(shot.get("end_state") or "").strip()) if p)
        spoken = "／".join(str(t.get("text") or "") for t in (shot.get("turns") or []))
        lines.append(f"镜头{position}：画面与事件「{text}」 台词「{spoken}」")
    mentioned = "；".join(f"镜{p}-{name}（候选来自{'、'.join(w)}）" for p, name, w in rows)
    prompt = (
        "下面是一集短剧的分镜。名单里是每个镜头被文字点到的具名人物（候选，不代表在场）。\n"
        "逐个判断每个人物在**这一镜的画面里**是否实际出现：\n"
        "- on_camera：人物本体在画面中出现（站着、坐着、走动、被拍到、被动作触及都算）\n"
        "- talked_about：只被台词或叙述**谈论/提及**（想起、担心、威胁转述、回忆里说到），本体不在画面里\n"
        "- absent：两种都不像，或无法判断\n"
        "注意：画面描述里「某人提到X」「某人想起X」「说到X」都是谈论，不是X在场；"
        "只有X自己出现在画面动作里才算 on_camera。只输出JSON。\n\n"
        + "\n".join(lines) + "\n\n候选名单：" + mentioned
    )
    try:
        out = ask_json([{"type": "text", "text": prompt}], _SCHEMA, name="presence_grades",
                       max_tokens=600, settings=settings)
    except Exception:  # noqa: BLE001 - no grades is not a planning failure; the rules stand in
        return {}
    grades: dict[int, dict[str, str]] = {}
    for row in out.get("grades") or []:
        try:
            position = int(row.get("shot"))
        except (TypeError, ValueError):
            continue
        name, grade = str(row.get("name") or ""), str(row.get("grade") or "")
        if 1 <= position <= len(shots) and name in everyone and grade in {"on_camera", "talked_about", "absent"}:
            grades.setdefault(position, {})[name] = grade
    return grades
