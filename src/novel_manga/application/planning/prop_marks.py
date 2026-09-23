"""Mark which catalogued props actually appear in each shot, on the judge's channel.

The planner's own model gets the props field and the instruction, but the pilot showed the
default planner model answering it empty on every stage of a chapter whose acquisition scene
plainly shows the object, while the judge marked the same stage correctly on the first try.
So after validation, one judge call per chapter marks the shots; the marks unite with whatever
the planner did say, and only names in the bible's prop list survive.  A judge outage marks
nothing - the chapter plans fine without marks, as books without props always have.
"""
from __future__ import annotations

from novel_manga.llm.client import ask_json

_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["marks"],
           "properties": {"marks": {"type": "array", "items": {"type": "object",
                                    "additionalProperties": False, "required": ["shot", "props"],
                                    "properties": {"shot": {"type": "integer"},
                                                   "props": {"type": "array", "items": {"type": "string"}}}}}}}


def mark_props(shots: list[dict], props: list, *, settings=None) -> dict[int, list[str]]:
    """{position in shots: [prop names]} for shots whose picture shows the prop itself.

    "Shows" is judged on the shot's own words: held, displayed, used, close-up - whatever
    name the description gives it.  Missing the mark loses a reference image; inventing one
    would seat a wrong picture, so the judge is asked to be strict.

    The judge numbers the shots by their position in this list, and says the number back:
    origin_index repeats when framing split one stage into several shots, so keying the
    answer by it would land one sibling's mark on all of them - and the lines the judge
    read would carry the same number twice.  A shot the judge leaves out, numbers twice or
    numbers outside the list keeps whatever the planner itself said, no more.
    """
    if not props or not shots:
        return {}
    from novel_manga.review.endpoints import judge_settings
    catalogue = {p.name: p for p in props}
    lines = []
    for position, shot in enumerate(shots, start=1):
        text = "；".join(p for p in (str(shot.get("motion_prompt") or "").strip(),
                                     str(shot.get("visual_prompt") or "").strip()) if p)
        lines.append(f"{position}. {text}")
    roster = "；".join(f"{p.name}（{p.appearance or p.category}）" for p in props)
    prompt = (
        f"本书已建档的剧情道具：{roster}。\n"
        "下面是一集短剧的分镜画面，每行开头的数字是镜头号。逐条判断：这个画面里道具**本体**是否实际出现"
        "（被拿着、被展示、被使用、被特写都算；画面描述用什么称呼不重要，玉石、那东西都可能指它）。"
        "不确定就不算出现。marks 是数组，每项形如 {\"shot\": 镜头号, \"props\": [道具名]}；"
        "本体没出现的镜头不要列，名字只能用名单里的。只输出JSON。\n\n" + "\n".join(lines)
    )
    try:
        out = ask_json([{"type": "text", "text": prompt}], _SCHEMA, name="prop_marks",
                       max_tokens=400, settings=settings or judge_settings())
    except Exception:  # noqa: BLE001 - no marks is not a planning failure
        out = {}
    judged: dict[int, list[str]] = {}
    for row in out.get("marks") or []:
        try:
            number = int(row.get("shot"))
        except (TypeError, ValueError):
            continue
        if 1 <= number <= len(shots):
            judged.setdefault(number, []).extend(str(n) for n in (row.get("props") or []))
    marks = {}
    for position, shot in enumerate(shots, start=1):
        # 规划器自己标的也算数（union）：判官是补漏，不是否决
        names = [n for n in dict.fromkeys([*(shot.get("props") or []), *(judged.get(position) or [])])
                 if n in catalogue]
        if names:
            marks[position] = names
    return marks
