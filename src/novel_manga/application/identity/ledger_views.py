"""ledger_views_thin responsibilities; existing evidence and identity policy."""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import html
import time
import novel_manga.application.identity.ledger_store as ledger_store
import novel_manga.entities.contracts as entity_contracts
import novel_manga.entities.evidence as entity_evidence

def build_index(novel_dir: Path) -> dict:
    """entity_index.json as the planner, packer and reviewer read it: proper forms per surviving record,
    contextual forms apart, mentions counted from the chapters, tier from the count."""
    ledger = ledger_store.Ledger(novel_dir)
    forms: dict[str, Counter] = {e["id"]: Counter() for e in ledger.entities}
    contextual: dict[str, set[str]] = {e["id"]: set() for e in ledger.entities}
    span: dict[str, list] = {e["id"]: [None, None] for e in ledger.entities}
    chapters = ledger.chapters_read()
    for c in chapters:
        for m in ledger_store.read_json(ledger.mentions_path(c), []):
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
                     "has_card": bool(e.get("asset_id")), "generic": not e.get("named", True) or len(e["canonical"]) < 2,
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


def snapshot(novel_dir: Path, chapter: int, segment_ids: list[str] | None = None, disclosed_through: int | None = None,
             ledger: "Ledger | None" = None, text: str | None = None) -> dict:
    """The casting sheet for one scene: who is present (by the chapter's own mentions), through which body they
    act, which names the audience may hear, who is a voice or only spoken of, and what must stay hidden.
    A caller that asks for many scenes passes its own ledger and chapter text instead of reloading both."""
    ledger = ledger or ledger_store.Ledger(novel_dir)
    disclosed_through = chapter if disclosed_through is None else disclosed_through
    text = ledger_store.novel_texts(novel_dir).get(chapter, "") if text is None else text
    window = None
    if segment_ids:
        segs = ledger_store.segment_texts(novel_dir, chapter)
        pieces = [segs[s] for s in segment_ids if s in segs]
        if pieces:  # the segments were cut from a cleaned copy: find them the way quotations are found
            head, tail = entity_evidence.locate(pieces[0][:40], text), entity_evidence.locate(pieces[-1][-40:], text)
            if head and tail and tail[1] > head[0]:
                window = (head[0], tail[1])
    accepted = [c for c in ledger.claims if c.get("status") == "accepted" and c["chapter"] <= chapter]
    body_of = {ledger.canonical(c["subject"]): ledger.canonical(c["object"]) for c in accepted
               if c["type"] == "occupies_body" and c["subject"] in ledger.by_id and c["object"] in ledger.by_id}
    hidden = [c for c in ledger.claims if c.get("hidden_from_reader") and c["chapter"] > disclosed_through]
    cast: dict[str, dict] = {}
    passage = text[window[0]:window[1]] if window else None
    for m in ledger_store.read_json(ledger.mentions_path(chapter), []):
        if passage is not None and m["form"] not in passage:
            continue  # a mention row is one form for the whole chapter: in the passage means the form occurs in it
        if not str(m["entity"]).startswith("e"):
            continue
        eid = ledger.canonical(m["entity"])
        body = body_of.get(eid, eid)
        row = cast.setdefault(eid, {"entity": eid, "name": ledger.name_of(eid), "presence": "mentioned", "names_spoken": set(),
                                    "body": body, "card": ledger.by_id.get(body, {}).get("asset_id")})
        if entity_contracts.PRESENCE_RANK[m["presence"]] > entity_contracts.PRESENCE_RANK[row["presence"]]:
            row["presence"] = m["presence"]
        if m["kind"] == "proper":
            row["names_spoken"].add(m["form"])
    for row in cast.values():
        row["names_spoken"] = sorted(row["names_spoken"])
        row["acts_through_other_body"] = row["body"] != row["entity"]
    present = set(cast)
    relations = [{"from": r["from"], "to": r["to"], "bases": r["bases"], "stance": r["stance"], "address": r["address"]}
                 for r in relation_summary(ledger, chapter)
                 if ledger.by_name.get(r["from"], {}).get("id") in present and ledger.by_name.get(r["to"], {}).get("id") in present
                 and (r["hidden_until"] is None or r["hidden_until"] <= disclosed_through)]
    secrets = [{"claim": c["id"], "type": c["type"], "subject": ledger.name_of(c["subject"]), "object": ledger.name_of(c["object"])} for c in hidden]
    secrets += [{"relation": True, "type": "/".join(r["bases"]) or r["stance"], "subject": r["from"], "object": r["to"]}
                for r in relation_summary(ledger, 10 ** 9) if r["hidden_until"] is not None and r["hidden_until"] > disclosed_through]
    return {"policy": entity_contracts.LEDGER_POLICY, "chapter": chapter, "segments": segment_ids,
            "cast": sorted(cast.values(), key=lambda r: -entity_contracts.PRESENCE_RANK[r["presence"]]), "relations": relations, "must_not_reveal": secrets}


def pending_pages(novel_dir: Path, texts: dict[int, str] | None = None) -> list[Path]:
    """One page per chapter with pending claims: the chapter with the quotations and the two records' mentions
    highlighted.  Uses LangExtract's visualiser when it is installed (same span model), plain marks otherwise."""
    ledger = ledger_store.Ledger(novel_dir)
    texts = texts or ledger_store.novel_texts(novel_dir)
    by_chapter: dict[int, list[dict]] = {}
    for c in ledger.claims:
        if c.get("status") == "pending":
            by_chapter.setdefault(c["chapter"], []).append(c)
    pages = []
    for chapter, claims in sorted(by_chapter.items()):
        text = texts.get(chapter, "")
        parties = {ledger.canonical(x) for c in claims for x in (c["subject"], c["object"]) if x in ledger.by_id}
        spans = [("claim", c["span"], f"{c['id']} {c['type']} {ledger.name_of(c['subject'])}→{ledger.name_of(c['object'])} [{c.get('verdict')}] {c.get('why', '')}")
                 for c in claims]
        spans += [("mention", m["span"], f"{ledger.name_of(ledger.canonical(m['entity']))} ({m['kind']}, {m['presence']})")
                  for m in ledger_store.read_json(ledger.mentions_path(chapter), []) if str(m["entity"]).startswith("e") and ledger.canonical(m["entity"]) in parties]
        out = ledger.base / f"pending_ch_{chapter:04d}.html"
        out.write_text(_highlight_page(f"{Path(novel_dir).name} 第 {chapter} 章：待确认 {len(claims)} 条", text, spans), encoding="utf-8")
        pages.append(out)
    return pages


def _highlight_page(title: str, text: str, spans: list[tuple[str, list, str]]) -> str:
    try:
        import langextract as lx
        from langextract import data
        doc = data.AnnotatedDocument(document_id=title, text=text, extractions=[
            data.Extraction(extraction_class=kind, extraction_text=text[s:e], char_interval=data.CharInterval(start_pos=s, end_pos=e),
                            attributes={"note": note}) for kind, (s, e), note in spans])
        page = lx.visualize(doc)
        return f"<h3>{html.escape(title)}</h3>" + str(getattr(page, "data", page))
    except ImportError:
        pieces, at = [], 0
        for kind, (s, e), note in sorted(spans, key=lambda x: x[1][0]):
            if s < at:
                continue
            colour = "#ffd" if kind == "mention" else "#fcc"
            pieces.append(html.escape(text[at:s]))
            pieces.append(f'<mark style="background:{colour}" title="{html.escape(note)}">{html.escape(text[s:e])}</mark>')
            at = e
        pieces.append(html.escape(text[at:]))
        legend = "".join(f"<li>{html.escape(note)}</li>" for kind, _, note in spans if kind == "claim")
        return (f"<meta charset='utf-8'><h3>{html.escape(title)}</h3><ul>{legend}</ul>"
                f"<pre style='white-space:pre-wrap;font-family:serif;line-height:1.7'>{''.join(pieces)}</pre>")
