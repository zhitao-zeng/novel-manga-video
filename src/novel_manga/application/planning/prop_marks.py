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
           "properties": {"marks": {"type": "array", "items": {"type": "array",
                                    "items": {"type": "string"}}}}}


def mark_props(shots: list[dict], props: list, *, settings=None) -> dict[int, list[str]]:
    """{origin_index: [prop names]} for shots whose picture shows the prop itself.

    "Shows" is judged on the shot's own words: held, displayed, used, close-up - whatever
    name the description gives it.  Missing the mark loses a reference image; inventing one
    would seat a wrong picture, so the judge is asked to be strict.
    """
    if not props or not shots:
        return {}
    from novel_manga.review.endpoints import judge_settings
    catalogue = {p.name: p for p in props}
    lines = []
    for shot in shots:
        text = "；".join(p for p in (str(shot.get("motion_prompt") or "").strip(),
                                     str(shot.get("visual_prompt") or "").strip()) if p)
        lines.append(f"{shot.get('origin_index')}. {text}")
    roster = "；".join(f"{p.name}（{p.appearance or p.category}）" for p in props)
    prompt = (
        f"本书已建档的剧情道具：{roster}。\n"
        "下面是一集短剧的分镜画面。逐条判断：这个画面里道具**本体**是否实际出现"
        "（被拿着、被展示、被使用、被特写都算；画面描述用什么称呼不重要，玉石、那东西都可能指它）。"
        "不确定就不算出现。marks 是逐镜头的道具名数组，没出现给空数组，长度与镜头数一致，"
        "名字只能用名单里的。只输出JSON。\n\n" + "\n".join(lines)
    )
    try:
        out = ask_json([{"type": "text", "text": prompt}], _SCHEMA, name="prop_marks",
                       max_tokens=400, settings=settings or judge_settings())
    except Exception:  # noqa: BLE001 - no marks is not a planning failure
        out = {}
    answered = list(out.get("marks") or [])
    answered += [[]] * (len(shots) - len(answered))   # 判官答得短（或没答）时，其余镜头只剩规划器自己的标注
    marks = {}
    for shot, marked in zip(shots, answered):
        # 规划器自己标的也算数（union）：判官是补漏，不是否决
        names = [n for n in dict.fromkeys([*(shot.get("props") or []), *(marked or [])])
                 if n in catalogue]
        if names:
            marks[int(shot.get("origin_index"))] = names
    return marks
