"""Carry the existing reading's character facts into casting, without guessing identities."""
from __future__ import annotations

from copy import deepcopy
import json


FACTS_FIELD = "character_facts"
_UNWRITTEN = {"", "未写明", "未详述", "不详", "未知", "未定"}


def _written(value: object) -> str:
    text = str(value or "").strip()
    return "" if text in _UNWRITTEN else text


def facts_from_export(export: dict, phases: dict | None = None) -> dict[str, dict]:
    """Keep source descriptions and citations from the same export that supplies the roster.

    Empty/unknown fields remain empty, so a designer may fill them.  Phase rows use the
    existing phase-cards-v1 shape; this function does not create or infer new phases.
    """
    phase_data = phases or export.get("phases") or {}
    phase_rows = phase_data.get("characters", phase_data)
    facts: dict[str, dict] = {}
    for person in export.get("characters", []):
        if not isinstance(person, dict):
            continue
        name = str(person.get("name") or "").strip()
        if not name:
            continue
        facts[name] = {
            "name": name,
            "aliases": list(person.get("aliases") or []),
            **{key: _written(person.get(key)) for key in ("gender", "age", "appearance", "wardrobe", "identity")},
            "evidence": deepcopy(person.get("evidence") or []),
            "looks": deepcopy(person.get("looks") or []),
            "phases": deepcopy(phase_rows.get(name) or person.get("phases") or []),
        }
        if not facts[name]["appearance"]:
            facts[name]["appearance"] = "；".join(
                look["text"] for look in facts[name]["looks"] if look.get("lasting") == "持久")
    return facts


def merge_character_facts(existing: dict, incoming: dict) -> dict[str, dict]:
    """A later reading of a book must not erase established facts with blank fields."""
    result = deepcopy(existing)
    for name, fact in incoming.items():
        old = result.get(name, {})
        merged = {**old, **deepcopy(fact)}
        for key in ("gender", "age", "appearance", "wardrobe", "identity"):
            if not _written(fact.get(key)) and _written(old.get(key)):
                merged[key] = old[key]
        for key in ("aliases", "evidence", "looks", "phases"):
            merged[key] = deepcopy(old.get(key) or [])
            for value in fact.get(key) or []:
                if value not in merged[key]:
                    merged[key].append(deepcopy(value))
        result[name] = merged
    return result


def facts_for_name(facts: dict, name: str, aliases: dict | None = None,
                   chapter: int | None = None) -> dict:
    """Resolve only exact names and already-confirmed aliases; an ambiguous form stays unbound."""
    canonical = name if name in facts else (aliases or {}).get(name)
    if canonical not in facts:
        owners = [key for key, row in facts.items() if name in (row.get("aliases") or [])]
        if len(owners) != 1:
            return {}
        canonical = owners[0]
    fact = deepcopy(facts[canonical])
    if chapter is not None:
        active = [phase for phase in fact.get("phases", [])
                  if int(phase["from"]) <= chapter
                  and (phase.get("to") is None or chapter <= int(phase["to"]))]
        if len(active) == 1:
            phase = active[0]
            for key in ("gender", "appearance", "wardrobe"):
                if _written(phase.get(key)):
                    fact[key] = phase[key]
            fact["active_phase"] = phase
            fact["evidence"] = [*fact.get("evidence", []), *deepcopy(phase.get("evidence") or [])]
        # A chapter request gets only its applicable phase, not future appearances to imitate.
        fact["phases"] = active
    return fact


def facts_prompt(facts: dict, names: list[str], aliases: dict | None = None,
                 chapter: int | None = None) -> str:
    rows = [fact for name in names if (fact := facts_for_name(facts, name, aliases, chapter))]
    if not rows:
        return ""
    return (
        "\n全书读书已经确认的人物事实与完整原文证据：\n"
        "有值的性别、年龄、外貌和服装是约束，不是可被旧角色模板替换的设计例子；性别未写明时留空，不从名字推断。"
        "只对空缺项做克制设计；实际寿龄与外观年龄不同，不按称号推老脸。"
        "原文明示的永久毁容、疤痕、残肢属于稳定外貌，必须保留；临时伤势不变成默认外貌。"
        "hair、face_anchors、palette、base_costume及role须和这些事实一致。"
        "phases沿用已确认的阶段范围，不把未来形态或一场戏的穿着套成全书常态。\n"
        + json.dumps(rows, ensure_ascii=False)
        + "\n"
    )


def apply_known_facts(row: dict, fact: dict) -> dict:
    """Keep the reading's explicit fields authoritative, while retaining designs for missing fields."""
    result = dict(row)
    for key in ("gender", "age", "appearance", "wardrobe"):
        if text := _written(fact.get(key)):
            result[key] = text
    if wardrobe := _written(fact.get("wardrobe")):
        result["base_costume"] = wardrobe
    return result
