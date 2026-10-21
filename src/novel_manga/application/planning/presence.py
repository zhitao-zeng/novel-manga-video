"""Use the existing chapter judge to distinguish visible people from mentions.

Visible dialogue structurally requires its speaker. Action participants may be
outside the frame and remain candidates for the judge rather than forced cast.
An unavailable judge leaves the declared visibility unchanged.
"""
from __future__ import annotations
import copy

from novel_manga.llm.client import ask_json
from novel_manga.review.endpoints import judge_settings
from novel_manga.planning import cast as pc_cast
from novel_manga.planning.context import PlannerContext
from novel_manga.planning.issues import PlanningCode, PlanningIssue

_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["grades"],
           "properties": {"grades": {"type": "array", "items": {"type": "object",
                                    "additionalProperties": False,
                                    "required": ["shot", "name", "grade"],
                                    "properties": {"shot": {"type": "integer"},
                                                   "name": {"type": "string"},
                                                   "grade": {"type": "string",
                                                             "enum": ["on_camera", "talked_about", "absent"]}}}}}}


def structural_on_camera(shot: dict) -> set[str]:
    """A visible spoken line requires its speaker; an action may cross the frame."""
    return {str(turn.get("speaker_name") or "") for turn in (shot.get("turns") or [])
            if turn.get("delivery_mode") == "visible_dialogue" and turn.get("speaker_name")}


def apply_presence_grades(shot: dict, judged: dict[str, str], names: list[str]) -> tuple[list[str], list[str]]:
    """Apply a judge result to every on-screen list, not only to the main cast."""
    structural = structural_on_camera(shot)
    cast = list(shot.get('characters') or [])
    promoted = []
    for name, grade in judged.items():
        if grade == 'on_camera' and name in names and name not in cast:
            cast.append(name)
            promoted.append(name)
    seated = set(cast) | set(shot.get('listeners') or []) | set(shot.get('in_frame') or [])
    removed = {name for name in seated if judged.get(name) in {'talked_about', 'absent'}
               and name not in structural and name in names}
    shot['characters'] = [name for name in cast if name not in removed]
    for field in ('listeners', 'in_frame'):
        if field in shot:
            shot[field] = [name for name in (shot.get(field) or []) if name not in removed]
    if 'in_frame' in shot:
        shot['in_frame'] = list(dict.fromkeys([*shot['in_frame'], *promoted]))
    if removed:
        shot['mentioned_only'] = list(dict.fromkeys([*(shot.get('mentioned_only') or []), *sorted(removed)]))
    return promoted, sorted(removed)


def issues_for_grades(shots, grades, names):
    """Return contradictions to the existing writer patch loop; keep its draft intact.

    They carry their own code: once the patches run out the writer's draft stands and the
    disagreement is reported (planning.decisions.decide_validation) - never a reason to re-plan a
    whole draft or to fail a chapter."""
    issues = []
    for position, shot in enumerate(shots, 1):
        added, removed = apply_presence_grades(copy.deepcopy(shot), grades.get(position) or {}, names)
        if added or removed:
            issues.append(PlanningIssue(PlanningCode.PRESENCE_DISAGREEMENT,
                f'在场复核与本镜名单不一致：复核认为应入镜{added}、应在画外或仅被提及{removed}。'
                '对照原文重新决定，并同时修改in_frame、起点、事件、末态和机位，不能只改名单。',
                stage=shot.get('label'), field='in_frame'))
    return issues


def candidates_for(shots, everyone, *, ctx=None):
    """Only ambiguous names need a model reading; visible speakers are structural facts."""
    ctx = ctx or PlannerContext()
    everyone = list(everyone)
    rows = []
    for position, shot in enumerate(shots, start=1):
        cast = set(shot.get('characters') or []) | set(shot.get('in_frame') or []) | set(shot.get('listeners') or [])
        structural = structural_on_camera(shot)
        candidates = pc_cast.presence_candidates(sorted(cast), shot, everyone, ctx=ctx)
        candidates.update({name: [{'field': 'cast', 'grade': 'on_camera'}] for name in cast - structural})
        for action in shot.get('actions') or []:
            for name in (action.get('actor'), action.get('target')):
                if name in everyone and name not in structural:
                    candidates.setdefault(name, [{'field': 'actions', 'grade': 'candidate'}])
        for name in sorted(candidates):
            if name not in structural:
                rows.append((position, name, sorted({str(e.get('field')) for e in candidates[name]})))
    return rows


def grade_presence(shots: list[dict], everyone: list[str], *, ctx: PlannerContext = None,
                   settings=None, log=None, roster=None, source='') -> dict[int, dict[str, str]]:
    """{shot position: {name: grade}} - the judged grade of every candidate the scan found.

    Returns {} when there is no judge or nothing to grade: the caller then keeps the rule-based
    grades.  Candidates only: names the scan found in the shot's own fields or lines, including declared cast and action participants; only explicitly visible speakers
    are already required by their delivery mode.

    `roster` gives the judge each name's appearance line from the bible, so it knows whose body
    is a hologram or a voice: the judge cannot tell 贾维斯 is bodiless from a name alone, and
    graded him on camera in a shot that only spoke about his crash.
    """
    ctx = ctx or PlannerContext()
    everyone = list(everyone)
    rows = candidates_for(shots, everyone, ctx=ctx)
    if not rows:
        return {}
    roster_text = ""
    if roster:
        described = "；".join(f"{name}：{str(roster[name]).strip()}"
                             for name in dict.fromkeys(name for _, name, _ in rows) if roster.get(name))
        if described:
            roster_text = "\n人物档案（判断谁有身体时以此为据）：\n" + described
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
        "档案注明无实体、全息、声纹之类的人物，只有当画面文字实际描写他显形（全息影像亮起、"
        "屏幕上浮现他的形象）时才算 on_camera——台词里说他死机了、坏了，只是谈论他。"
        "明确写在画外的人不能判on_camera；同处一个房间并不意味着本镜头能看见。"
        "只有真正在画面的人才是 on_camera。"
        + roster_text + ('\n本集原文：\n' + source if source else '') +
        "\n\n只输出JSON。\n"
        + "\n".join(lines) + "\n\n候选名单：" + mentioned
    )
    try:
        out = ask_json([{"type": "text", "text": prompt}], _SCHEMA, name="presence_grades",
                       max_tokens=max(600, min(6000, 80 * len(rows))),
                       settings=settings or judge_settings())
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


def grades_of(verdict, candidates):
    expected = {}
    for position,name,_ in candidates:
        expected.setdefault(str(position),set()).add(name)
    grades = verdict.get('grades')
    if not isinstance(grades,dict) or set(grades)!=set(expected):
        raise ValueError('在场复核缺少本镜判断')
    for position,names in expected.items():
        if not isinstance(grades[position],dict) or set(grades[position])!=names or any(
                grade not in {'on_camera','talked_about','absent'}for grade in grades[position].values()):
            raise ValueError('在场复核人物判断不完整')
    if not isinstance(verdict.get('marks'),list):
        raise ValueError('道具标注不完整')
    return {int(position):dict(values)for position,values in grades.items()}


def review_visibility(shots, everyone, *, ctx=None, roster=None, props=(), previous=None, settings=None):
    """Read only the current frame's bodies and objects; never infer casting from plot promises."""
    import json
    from novel_manga.llm.client import obj
    from .prop_marks import _SCHEMA as prop_schema
    candidates = candidates_for(shots,everyone,ctx=ctx)
    relevant = {position for position,_,_ in candidates}
    names = {name for _,name,_ in candidates}
    inputs = {'shots':[{'shot':i,'in_frame':s.get('in_frame'),'candidate_characters':s.get('characters'),
                'camera':s.get('camera'),'picture':{k:s.get(k)for k in ('visual_prompt','motion_prompt','end_state')},
                'actions':s.get('actions'),'authored_props':s.get('props')}
              for i,s in enumerate(shots,1)if props or i in relevant],
              'candidates':candidates,'roster':{k:v for k,v in (roster or {}).items()if k in names},
              'props':[{'name':p.name,'appearance':p.appearance or p.category}for p in props]}
    inputs=json.loads(json.dumps(inputs,ensure_ascii=False))
    if previous and previous.get('inputs')==inputs:
        grades_of(previous,candidates)
        return previous
    if not candidates and not props:
        return {'inputs':inputs,'grades':{},'marks':[]}
    fields={}
    for position,name,_ in candidates:
        fields.setdefault(str(position),{})[name]={'type':'string','enum':['on_camera','talked_about','absent']}
    schema=obj({'grades':obj({position:obj(names)for position,names in fields.items()}),
                'marks':prop_schema['properties']['marks']})
    prompt=('只核对当前镜头里人物本体和道具本体能否被看见，不判断剧情应选哪些人物，不重写镜头。'
        'candidate_characters与candidates只是候选；in_frame是编剧声明的入画名单，结合picture和camera核对。'
        'grades逐个判断：on_camera=本体确实在本镜画面；talked_about=仅被提及；absent=不在本镜或依据不足。'
        '同处一个房间不等于本镜看得见；动作的受事或视线所向可以在画外，不能仅凭动作指向就判目标入画。'
        '画外声和心声不强制人物入画；roster中的无实体/全息人物，只有本镜明确显形才算on_camera。'
        '明确的可见发言者已由结构校验确定，不再列为候选。只用本镜证据，不从全章剧情推测应出镜者。'
        'marks标注被持有、展示、使用或特写的道具本体，名字仅限props，不确定或仅提及则不标。'
        '镜号使用shot，不能把共享origin_index的兄弟镜混合。只输出JSON。\n'+json.dumps(inputs,ensure_ascii=False))
    answer=ask_json([{'type':'text','text':prompt}],schema,name='visibility_review',
                    max_tokens=3000,settings=settings or judge_settings())
    grades_of(answer,candidates)
    return {'inputs':inputs,**answer}
