"""Experimental source-vs-script diagnosis. Does not change production routing."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from repair_flow_thin import read
from thin_review import ask_json, snapshot_block

CAUSES = ["script_mismatch", "request_mismatch", "generation_mismatch", "uncertain"]
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["cause", "reason", "source_quotes", "script_quote", "stage_indexes"],
          "properties": {
              "cause": {"type": "string", "enum": CAUSES}, "reason": {"type": "string"},
              "source_quotes": {"type": "array", "items": {"type": "string"}},
              "script_quote": {"type": "string"},
              "stage_indexes": {"type": "array", "items": {"type": "integer"}},
          }}
RULES = """你判断一段动画视频该修分镜还是只重拍，不负责重新判画面是否有错。
给定原文、身份账本、实际分镜和中英文生成指令、精判确认的画面问题：
- script_mismatch：原文/账本要求的动作主体、对象、出场人物或事件在分镜里已经写错、漏掉或表达冲突。
- request_mismatch：原始分镜正确，但打包后的中文/英文生成指令、Subject编号或参考图绑定写错/漏了。优先修请求，不归入纯重拍。
- generation_mismatch：分镜和实际中英文指令已经清楚表达原文要求，只是生成视频没有执行（例如无端复制人物、换脸、动作安错人）。
- uncertain：证据不足、身份/伪装/画外情况不明；不要硬分成视频执行错。
先核对每个英文Subject编号绑定的是谁，再核对动作主体；中文写医生开锁，英文却让绑定主角的Subject开锁，就属于请求错误。
原始分镜要求关键人物在画面中，但实际Subject/参考图中遗漏、又不是合法画外或无名配角，也属于请求错误。
“角色”“人物”“Subject”都是绑定术语，可包括猫等动物；不能仅因这些用词就判物种写错，要检查实际外形和动作描述。
必须引用原文原句与实际分镜/指令中的原句，标明阶段号。不要把精判的错误描述当作原文证据。
画面演员长错、服装不同本身不证明分镜写错；分镜要求的演员/动作本就错时也不能靠重拍蒙过。
只输出指定 JSON，不写新剧本、不改台词。
"""


def clip_context(novel: Path, episode: int, cid: str) -> dict:
    directory = novel / f"{novel.name}_{episode}"
    plan = read(directory / "clip_plan.json", {})
    clip = next(c for c in plan["clips"] if c["clip_id"] == cid)
    script = read(directory / "chapter_script.json", {})
    stages = [{**s,'origin_index':s.get('index',i)} for i,s in enumerate(script.get('shots',[]),1)
              if s.get('index',i) in clip.get('shot_indexes',[])]
    segments = read(directory / "segments.json", [])
    passage = "\n".join(s["text"] for s in segments if s.get("segment_id") in clip.get("segment_ids", []))
    verdict = read(directory / "episode_review.json", {}).get("clips", {}).get(cid) or {}
    aliases = read(novel / "bible_aliases.json", {})
    subjects = [{"subject": int(s), "name": n, "picture": int(p)} for s,n,p in
                re.findall(r"<Subject (\d+)> is the character ([^,\n]+), shown in <Picture (\d+)>", clip.get("prompt_h3", ""))]
    return {"episode": episode, "clip_id": cid, "passage": passage, "stages": stages,
            "prompt": clip.get("prompt", ""), "prompt_h3": clip.get("prompt_h3", ""),
            "ledger": snapshot_block(clip, directory), "issue": verdict.get("story_issue") or verdict.get("identity_issue"),
            "instruction": verdict.get("feedback", ""), "subjects": subjects,
            "references": clip.get("references", []),'crowd_roles':clip.get('crowd_roles',{}),
            "aliases": {a:n for a,n in aliases.items() if a in passage or a in clip.get('prompt','')}}


def validate_diagnosis(answer: dict, context: dict) -> dict:
    reasons = []
    if answer.get("cause") not in CAUSES:
        reasons.append("unknown cause")
    sources = answer.get("source_quotes") or []
    if not sources or any(not q or q not in context["passage"] for q in sources):
        reasons.append("source evidence is not a literal quote")
    script = json.dumps(context["stages"], ensure_ascii=False) + "\n" + context["prompt"] + "\n" + context["prompt_h3"]
    quote = answer.get("script_quote") or ""
    if not quote or quote not in script:
        reasons.append("script evidence is not a literal quote")
    indexes = {s["origin_index"] for s in context["stages"]}
    if not answer.get("stage_indexes") or not set(answer["stage_indexes"]) <= indexes:
        reasons.append("stage evidence is absent or out of range")
    return {**answer, "cause": "uncertain" if reasons else answer["cause"], "evidence_errors": reasons}


def diagnose(context: dict) -> dict:
    started = time.monotonic()
    if not context["passage"] or not context["stages"]:
        return {"cause": "uncertain", "reason": "missing source or stages", "elapsed_seconds": 0}
    try:
        answer = ask_json([{"type": "text", "text": RULES + "\n" + json.dumps(context, ensure_ascii=False)}],
                          SCHEMA, name="repair_cause", max_tokens=1200)
        result = validate_diagnosis(answer, context)
    except Exception as error:
        result = {"cause": "uncertain", "reason": f"{type(error).__name__}: {str(error)[:180]}"}
    return {**result, "elapsed_seconds": round(time.monotonic() - started, 3)}


def numbered_evidence(context: dict) -> tuple[dict, dict]:
    source = {f"S{i}": text for i, text in enumerate(re.findall(r"[^。！？\n]+[。！？]?", context["passage"]), 1) if text.strip()}
    script = {}
    for i, stage in enumerate(context["stages"], 1):
        for key in ["visual_prompt", "motion_prompt", "start_state", "end_state", "characters", "actions", "turns", "avoid"]:
            value = stage.get(key)
            if value:
                script[f"D{i}_{key}"] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    for prefix, field in [("P", "prompt"), ("H", "prompt_h3")]:
        for i, line in enumerate(context[field].splitlines(), 1):
            if line.strip():script[f"{prefix}{i}"] = line
    return source, script


def diagnose_numbered(context: dict) -> dict:
    source, script = numbered_evidence(context)
    if not source or not script or not context["stages"]:
        return {"cause": "uncertain", "reason": "missing source/stage mapping", "elapsed_seconds": 0}
    schema = {"type": "object", "additionalProperties": False, "required": ["cause", "reason", "evidence"], "properties": {
        "cause": {"type": "string", "enum": CAUSES}, "reason": {"type": "string"},
        "evidence": {"type": "array", "minItems": 1, "maxItems": 3, "items": {"type": "object", "additionalProperties": False,
            "required": ["source_id", "script_id", "explanation"], "properties": {
                "source_id": {"type": "string", "enum": list(source)}, "script_id": {"type": "string", "enum": list(script)},
                "explanation": {"type": "string"}}}}}}
    question = (RULES + "\n这次通过编号引用证据，不抄写原句或阶段号。source_id只选S编号，script_id只选D/P/H编号。"
                "每对编号解释两者是否一致；reason给出结论。\n" + json.dumps({"原文": source, "分镜及实际请求": script,
                "身份账本": context["ledger"], "精判已确认的画面问题": context["issue"],
                "实际Subject对应":context.get('subjects',[]), "实际参考图":context.get('references',[]),
                '有原文人数依据的群体角色（参考图只提供制服，不要求共用人物编号或脸）':context.get('crowd_roles',{}),
                "称呼对应":context.get('aliases',{})}, ensure_ascii=False))
    started = time.monotonic()
    try:
        answer = ask_json([{"type": "text", "text": question}], schema, name="repair_cause_numbered", max_tokens=1000)
        pairs = answer.get("evidence") or []
        valid = bool(pairs) and answer.get("cause") in CAUSES and all(p.get("source_id") in source and p.get("script_id") in script for p in pairs)
        result = {**answer, "cause": answer["cause"] if valid else "uncertain", "evidence_valid": valid,
                  "quoted_evidence": [{**p, "source": source.get(p.get("source_id")), "script": script.get(p.get("script_id"))} for p in pairs]}
    except Exception as error:
        result = {"cause": "uncertain", "reason": f"{type(error).__name__}: {str(error)[:150]}", "evidence_valid": False}
    return {**result, "elapsed_seconds": round(time.monotonic() - started, 3)}
