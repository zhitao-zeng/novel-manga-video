"""Review what the outline promised against what the viewer will actually learn."""
from __future__ import annotations

import json

from novel_manga.llm.client import ask_json
from novel_manga.llm.config import endpoint_settings
from novel_manga.planning.issues import PlanningCode, PlanningIssue


SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdicts"],
          "properties": {"verdicts": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "required": ["promise", "status", "reason"],
              "properties": {"promise": {"type": "string"},
                             "status": {"type": "string", "enum": ["delivered", "missing", "changed", "unclear"]},
                             "reason": {"type": "string", "maxLength": 180}}}}}}


def section_of(outline: dict) -> str:
    return str(((outline.get("sections") or {}).get("retained_dialogue") or "")).strip()


def interpret(verdict: dict, section: str) -> list[PlanningIssue]:
    if verdict.get("section") != section:
        raise ValueError("保留内容审查不属于当前提纲")
    rows = verdict.get("verdicts")
    if not isinstance(rows, list) or (section and not rows):
        raise ValueError("保留内容审查没有逐项结论")
    issues = []
    for row in rows:
        if row.get("status") == "delivered":
            continue
        if row.get("status") not in {"missing", "changed", "unclear"}:
            raise ValueError("保留内容审查返回未知状态")
        issues.append(PlanningIssue(
            PlanningCode.RETAINED_LINE_LOST,
            f"提纲承诺「{str(row.get('promise') or '')[:50]}」未落实：{str(row.get('reason') or '')[:800]}；"
            "按原文和提纲补进观众能听见或看见的表达", field="turns"))
    return issues


def review(section: str, shots: list[dict], source: str) -> tuple[dict, list[PlanningIssue]]:
    """One text review; structural gates already ran before this is called."""
    if not section:
        return {"section": "", "verdicts": []}, []
    evidence = [{"shot": shot.get("label") or shot.get("shot_id") or index,
                 "scene": shot.get("scene_id") or shot.get("location"),
                 "location": shot.get("location"),
                 "source_quote": shot.get("source_quote"),
                 "characters": shot.get("characters"),
                 "extras": shot.get("extras"),
                 "props": shot.get("props"),
                 "scene_objects": shot.get("scene_objects"),
                 "wears": shot.get("wears"),
                 "actions": shot.get("actions"),
                 "light": shot.get("light"),
                 "picture": "；".join(str(shot.get(k) or "") for k in ("visual_prompt", "motion_prompt", "end_state")),
                 "turns": [{k: turn.get(k) for k in ("speaker_name", "delivery_mode", "inner_monologue", "text")}
                           for turn in shot.get("turns") or []]}
                for index, shot in enumerate(shots, 1)]
    prompt = (
        "你核对一集短剧的第一遍提纲与第二遍实际分镜。逐条判断提纲 retained_dialogue 承诺的关键问答、"
        "动机、决定、笑点或叙事信息是否真正让观众知道。完整阅读该段文字，不能只挑引号中的句子、"
        "不能忽略短句、长句或第八条之后的承诺。允许等义改写和删去赘词；不要求逐字重复原文。"
        "必须核对谁说、在哪场、是否当面对需要知道的人表达、肯定或否定是否反转，以及镜头动作能否承担"
        "该信息。画面描述里写了'说/决定/解释'，但 turns 没有实际台词，不能算观众听见；"
        "人物心声不等于把消息告诉现场的另一人。不要因为共享几个字就判保留，也不要因为换词就判遗漏。"
        "小说的第三人称心理叙述改成角色本人的心声时，必须保持指代正确：角色想的是自己，不能念成对方的状态；"
        "例如原文主语是当前说话人的‘他心情好’，心声仍说‘他心情好’会改变所指，应报告 changed。"
        "即使提纲没有选中，也从原文找出直接支撑后续决定的条件、拒绝或威胁；若删掉它会让决定失去原因，"
        "将它作为额外一项写入 verdicts，核对是否通过对白或可见动作让观众知道。不要把每句原文都列入，"
        "只检查真正改变下一步行动的因果信息。还要核对相邻镜头是否把同一次进入、离开、召唤或击打拍了两次，"
        "穿戴物是否同时写入 extras 与 props、wears 是否与原文中的穿戴状态矛盾，以及场景地点是否与镜头内容冲突。"
        "extras 是无卡人物或动物，props 是物件，wears 是人物穿戴状态；相邻场景若只过了几分钟，"
        "light 不能从夜晚变成白天。发现这些问题也单独列为 changed。"
        "道具是否区分应结合scene_objects、动作和画面文本一起判断；明确写出第二台无人空机甲就已区分，"
        "不要仅因props只列同款资产就要求新增型号或另一张卡，更不得猜测原文没有的型号。"
        "输出 verdicts，对每项明确承诺和上述遗漏的因果信息给出 delivered/missing/changed/unclear 和具体依据；"
        "reason 每项只写100字以内的具体结论与镜号，不反复讨论、不抄整段原文。"
        "没有可核对的承诺时只输出一项 delivered，说明该段只有选材说明。不能用空列表代替审查。\n"
        + json.dumps({"retained_dialogue": section, "source": source, "shots": evidence}, ensure_ascii=False)
    )
    answer = ask_json([{"type": "text", "text": prompt}], SCHEMA,
                      name="retained_content_review", max_tokens=3000, timeout=180,
                      retry_truncated=False, settings=endpoint_settings('local'))
    verdict = {"section": section, "verdicts": answer.get("verdicts")}
    return verdict, interpret(verdict, section)
