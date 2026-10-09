"""Derive identity indexes from an already loaded ledger; no files or model calls."""
from __future__ import annotations
from collections import Counter
import time
from novel_manga.entities import evidence as entity_evidence


def build_index(ledger) -> dict:
    """Keep the existing index schema and ranking, using the owner's chapter caches."""
    forms: dict[str, Counter] = {e["id"]: Counter() for e in ledger.entities}
    contextual: dict[str, set[str]] = {e["id"]: set() for e in ledger.entities}
    span: dict[str, list] = {e["id"]: [None, None] for e in ledger.entities}
    chapters = ledger.chapters_read()
    for c in chapters:
        for m in ledger.mentions_by_chapter.get(c, []):
            if not str(m.get("entity", "")).startswith("e"):
                continue
            eid = ledger.canonical(m["entity"])
            if eid not in forms:
                continue
            if m.get("kind") == "proper":
                forms[eid][m["form"]] += int(m.get("count", 1))
            else:
                contextual[eid].add(m["form"])
            span[eid][0] = c if span[eid][0] is None else min(span[eid][0], c)
            span[eid][1] = c if span[eid][1] is None else max(span[eid][1], c)
    merged_into: dict[str, list[str]] = {}
    for x in ledger.merges:
        merged_into.setdefault(x["into"], []).append(x["from"])
    rows = []
    for e in ledger.entities:
        if e["status"] != "active":
            continue
        rows.append({"name": e["canonical"], "id": e["id"], "asset_id": e.get("asset_id"), "role": e.get("role", ""),
                     "has_card": bool(e.get("asset_id")), "generic": entity_evidence.generic_record(e),
                     "forms": dict(forms[e["id"]].most_common()), "contextual_forms": sorted(contextual[e["id"]]),
                     "mentions": sum(forms[e["id"]].values()), "first_chapter": span[e["id"]][0], "last_chapter": span[e["id"]][1],
                     "merged": merged_into.get(e["id"], [])})
    ranked = sorted((r for r in rows if "主角" not in r["role"] and not r["generic"]), key=lambda r: -r["mentions"])
    major = {r["name"] for r in ranked[:12]}
    for r in rows:
        r["tier"] = ("extra" if r["generic"] else "lead" if "主角" in r["role"] else "major" if r["name"] in major
                     else "minor" if r["mentions"] >= 20 else "extra")
    return {"policy": "entity-index-v3-ledger", "built_at": time.strftime("%Y-%m-%d %H:%M:%S"), "chapters": len(chapters),
            "characters": rows, "relations": relation_summary(ledger, chapters[-1] if chapters else 0), "ambiguous_forms": {}}


def relation_summary(ledger: "Ledger", through: int) -> list[dict]:
    """One row per directed pair: the bases ever stated, the stance as of the latest chapter, the address forms,
    and the chapters it spans - what a scene needs to know about two people who are both in it."""
    pairs: dict[tuple[str, str], dict] = {}
    for r in ledger.relations_through(through):
        row = pairs.setdefault((r["from"], r["to"]), {"from": ledger.name_of(r["from"]), "to": ledger.name_of(r["to"]), "bases": Counter(),
                                                       "stance": "unknown", "stance_chapter": 0, "address": [], "first_chapter": r["chapter"],
                                                       "last_chapter": r["chapter"], "hidden_until": None})
        if r["base"] not in ("none", "unknown"):
            row["bases"][r["base"]] += 1
        if r["stance"] != "unknown" and r["chapter"] >= row["stance_chapter"]:
            row["stance"], row["stance_chapter"] = r["stance"], r["chapter"]
        if r["address"] and r["address"] not in row["address"]:
            row["address"].append(r["address"])
        row["first_chapter"], row["last_chapter"] = min(row["first_chapter"], r["chapter"]), max(row["last_chapter"], r["chapter"])
        if r["hidden_from_reader"]:
            row["hidden_until"] = max(row["hidden_until"] or 0, r["chapter"])
    return [{**row, "bases": dict(row["bases"].most_common())} for row in pairs.values()]

