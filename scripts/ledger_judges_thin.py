"""ledger_judges_thin responsibilities; existing evidence and identity policy."""
from __future__ import annotations
from novel_manga.review.endpoints import judge_settings
from novel_manga.model_client import ask_json
import novel_manga.entities.contracts as entity_contracts
import novel_manga.entities.evidence as entity_evidence

def extract_chapter(chapter: int, chapter_text: str, candidates: list[dict], forms: dict[str, set[str]]) -> dict:
    """One reading; a chapter so crowded that the answer overflows the budget is read again with a lean brief."""
    prompt = entity_evidence.extract_prompt(candidates, forms, chapter_text)
    try:
        answer = ask_json([{"type": "text", "text": prompt}], entity_contracts.EXTRACT_SCHEMA, name="entity_extract", max_tokens=4500, settings=judge_settings())
    except Exception as error:  # noqa: BLE001
        if "truncated" not in str(error):
            return {"chapter": chapter, "error": f"{type(error).__name__}: {error}"[:200]}
        try:
            answer = ask_json([{"type": "text", "text": prompt + entity_contracts.LEAN_NOTE}], entity_contracts.EXTRACT_SCHEMA, name="entity_extract", max_tokens=4500, settings=judge_settings())
            answer["lean"] = True
        except Exception as again:  # noqa: BLE001
            return {"chapter": chapter, "error": f"{type(again).__name__}: {again}"[:200]}
    return {"chapter": chapter, "candidates": [e["id"] for e in candidates], **answer}


def judge_link(form: str, evidence: str, assigned: dict, options: list[dict], forms: dict[str, set[str]]) -> dict:
    """The model put a written form on a record whose name it shares nothing with (路易斯小姐 on 那位女士): asked
    again with the sentence and the records whose names the form does carry.  Answer: an id, NEW, or UNCERTAIN."""
    rows = "\n".join(f"[{e['id']}] {entity_evidence.describe_record(e)}；已见写法：{'、'.join(sorted(forms.get(e['id'], set()))[:6])}" for e in [assigned, *options])
    text = (f"原文这句里的写法「{form}」指的是下面哪条记录？只输出 JSON {{entity, why}}，entity 填记录编号；都不是但确实是个新人物填 NEW；"
            f"无法确定填 UNCERTAIN。写法带名字时，优先名字对得上的记录；不是名字的泛称记录（吟游诗人、那位女士）只有原文明说是同一人才选。"
            f"\n原文：{evidence}\n候选记录：\n{rows}")
    try:
        return ask_json([{"type": "text", "text": text}], entity_contracts.RELINK_SCHEMA, name="entity_relink", max_tokens=200, settings=judge_settings())
    except Exception as error:  # noqa: BLE001
        return {"entity": "UNCERTAIN", "why": f"error {type(error).__name__}"}


def judge_claim(claim: dict, subject: str, object_: str) -> dict:
    """Does the quotation support this relation between these two records?  Both sides are described (not just
    named) so that a right sentence attached to the wrong person - "占据了别人身体" resolved to the wrong body -
    reads as insufficient."""
    text = (entity_contracts.VERDICT_RULES + f"关系类型：{claim['type']}（叙事范围：{claim['scope']}）\n主体（subject）：{subject}\n客体（object）：{object_}\n"
            f"证据原文：{claim['evidence']}\n这句证据是否明确支持“主体 {claim['type']} 客体”这条关系，而且客体确实是这条记录、不是别人？")
    try:
        return ask_json([{"type": "text", "text": text}], entity_contracts.VERDICT_SCHEMA, name="entity_verdict", max_tokens=200, settings=judge_settings())
    except Exception as error:  # noqa: BLE001
        return {"verdict": "insufficient", "why": f"error {type(error).__name__}"}


def judge_same(a: dict, b: dict, forms_a: set[str], forms_b: set[str], note: str = "") -> dict:
    """Are two records one person?  Asked when a new record's name shares a piece with an existing one (the
    read-ahead extracts chapters before the ledger has the people the previous chapter added), and when one
    written form bridges two records (艾琳娜·路易斯 for 艾琳娜 and 多洛茜·路易斯)."""
    text = (entity_contracts.SAME_RULES + f"记录甲：{entity_evidence.describe_record(a)}；已见写法：{'、'.join(sorted(forms_a))}\n"
            f"记录乙：{entity_evidence.describe_record(b)}；已见写法：{'、'.join(sorted(forms_b))}" + (f"\n本章原文：{note}" if note else ""))
    try:
        return ask_json([{"type": "text", "text": text}], entity_contracts.SAME_SCHEMA, name="entity_same", max_tokens=200, settings=judge_settings())
    except Exception as error:  # noqa: BLE001
        return {"same": "unsure", "why": f"error {type(error).__name__}"}


def judge_settle(pack: str, rules: str) -> dict:
    try:
        return ask_json([{"type": "text", "text": rules + "（why 不超过 80 字）\n\n" + pack}], entity_contracts.VERDICT_SCHEMA, name="entity_settle", max_tokens=600, settings=judge_settings())
    except Exception as error:  # noqa: BLE001
        return {"verdict": "insufficient", "why": f"error {type(error).__name__}"}


def judge_generic(e: dict, rows: list[dict]) -> dict:
    others = "\n".join(f"[{o['id']}] {entity_evidence.describe_record(o)}" for o in rows if o["id"] != e["id"])
    text = (entity_contracts.SAME_RULES + f"目标记录：[{e['id']}] {entity_evidence.describe_record(e)}\n候选记录：\n{others}\n"
            "只输出 JSON {same_ids, why}：same_ids 是与目标记录确为同一个存在的候选编号列表（没有就空数组）。只凭描述能确定的才算。")
    try:
        return ask_json([{"type": "text", "text": text}], entity_contracts.DEDUP_SCHEMA, name="entity_dedup", max_tokens=500, settings=judge_settings())
    except Exception as error:  # noqa: BLE001
        return {"same_ids": [], "why": f"error {type(error).__name__}"}
