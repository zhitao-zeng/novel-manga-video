"""ledger_extraction_thin responsibilities; existing evidence and identity policy."""
from __future__ import annotations

import novel_manga.application.identity.ledger_judges as ledger_judges
import novel_manga.application.identity.ledger_store as ledger_store
import novel_manga.entities.contracts as entity_contracts
import novel_manga.entities.evidence as entity_evidence

def extract(ledger, chapter: int, text: str, force: bool = False) -> dict:
    """One model call, safe in a pool: the candidates are a snapshot of the ledger as it is now.  The reading
    remembers how far the ledger reached, so that resolve_chapter can tell when it was made too early."""
    raw = None if force else ledger_store.read_json(ledger.raw_path(chapter), None)
    if raw is not None and not raw.get("error"):
        return raw
    with ledger.lock:
        entities = [dict(e) for e in ledger.entities]
        forms = {k: set(v) for k, v in ledger.forms.items()}
        recent = {ledger.canonical(eid) for eid, seen in ledger.recent.items() if 0 <= chapter - seen <= entity_contracts.RECENT_WINDOW}
    raw = ledger_judges.extract_chapter(chapter, text, entity_evidence.offered(entities, forms, text, recent), forms)
    raw["known_through"] = max((entity_evidence._number(e["id"]) for e in entities), default=0)
    if not raw.get("error"):
        ledger_store.write_json(ledger.raw_path(chapter), raw)
    return raw


def stale(ledger, raw: dict, text: str) -> list[dict]:
    """Records that came into the ledger after this reading was made, are named in this chapter, and were not
    handled right by the early reading: a form carrying their name was put on some other record, or left
    UNCERTAIN.  (A NEW: with their name resolves to them by name and needs no second reading.)  The model never
    saw these records, so what it did with those names was a guess (多洛茜·路易斯小姐 put on 吟游诗人)."""
    known = raw.get("known_through")
    if known is None or raw.get("fresh"):
        return []
    late = []
    for e in ledger.entities:
        if entity_evidence._number(e["id"]) <= known or e["status"] != "active":
            continue
        keys = {k for k in entity_evidence.retrieval_keys(e["canonical"]) | ledger.forms.get(e["id"], set()) if len(k) >= 2 and k not in entity_contracts.WEAK_KEYS}
        if not any(k in text for k in keys):
            continue
        for m in raw.get("mentions") or []:
            form = str(m.get("form") or "")
            if not any(k in form for k in keys):
                continue
            ref = str(m.get("entity") or "")
            if ref.startswith("NEW:") and ledger._resolve_ref(ref, {}) == ledger.canonical(e["id"]):
                continue  # named it, and the name resolves to this very record
            if ref in ledger.by_id and entity_evidence.shares_a_piece(form, ledger.by_id[ledger.canonical(ref)], ledger.forms):
                continue  # put on a record whose name it carries: not a stale guess
            late.append(e)
            break
    return late
