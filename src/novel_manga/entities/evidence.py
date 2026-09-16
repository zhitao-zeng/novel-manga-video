"""entities.evidence responsibilities; existing evidence and identity policy."""
from __future__ import annotations
import re
import novel_manga.entities.contracts as entity_contracts

def normalize(text: str) -> str:
    return re.sub(r"[\s“”\"'‘’「」『』…—\-–·,.，。！？!?；;：:（）()]", "", text or "")


def locate(evidence: str, chapter_text: str) -> tuple[int, int] | None:
    """Where the quotation sits in the chapter (character span), punctuation and spacing aside; None if absent."""
    piece = normalize(evidence)
    if len(piece) < 4:
        return None
    if evidence in chapter_text:
        start = chapter_text.index(evidence)
        return start, start + len(evidence)
    raw_positions = [i for i, ch in enumerate(chapter_text) if normalize(ch)]
    flat = "".join(chapter_text[i] for i in raw_positions)
    at = flat.find(piece)
    if at < 0:
        return None
    return raw_positions[at], raw_positions[at + len(piece) - 1] + 1


def ground_relations(chapter: int, chapter_text: str, raw: dict, dropped: list[dict]) -> list[dict]:
    rows = []
    for r in raw.get("relations") or []:
        span = locate(r.get("evidence", ""), chapter_text)
        if span is None or r.get("base") not in entity_contracts.RELATION_BASES or r.get("stance") not in entity_contracts.RELATION_STANCES:
            dropped.append({"chapter": chapter, "what": "relation", "why": "证据不在本章或类别不合法"})
            continue
        rows.append({"chapter": chapter, "from": str(r.get("from") or "").strip(), "to": str(r.get("to") or "").strip(), "base": r["base"],
                     "stance": r["stance"], "address": str(r.get("address") or "").strip()[:12], "hidden_from_reader": bool(r.get("hidden_from_reader")),
                     "evidence": str(r.get("evidence"))[:160], "span": list(span)})
    return rows


def ground(chapter: int, chapter_text: str, raw: dict) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Keep only what the chapter backs: a mention's form must occur, every quotation must be found."""
    mentions, news, claims, dropped = [], [], [], []
    for m in raw.get("mentions") or []:
        form = str(m.get("form") or "").strip()
        if not form or form not in chapter_text:
            dropped.append({"chapter": chapter, "what": "mention", "form": form, "why": "写法不在本章"})
            continue
        span = locate(m.get("evidence", ""), chapter_text)
        if span is None:
            span = (chapter_text.index(form), chapter_text.index(form) + len(form))
        mentions.append({"chapter": chapter, "form": form, "entity": str(m.get("entity") or "UNCERTAIN").strip(),
                         "entity_name": str(m.get("entity_name") or "").strip(),
                         "kind": m.get("kind") or "proper", "presence": m.get("presence") or "on_stage",
                         "count": chapter_text.count(form), "span": list(span), "evidence": str(m.get("evidence") or "")[:120]})
    for n in raw.get("new_entities") or []:
        name = str(n.get("name") or "").strip()
        name = name[4:].strip() if name.startswith("NEW:") else name
        span = locate(n.get("evidence", ""), chapter_text)
        if not name or span is None:
            dropped.append({"chapter": chapter, "what": "new_entity", "name": name, "why": "证据不在本章"})
            continue
        news.append({"chapter": chapter, "name": name, "kind": n.get("kind") or "person", "named": bool(n.get("named", True)),
                     "description": str(n.get("description") or "")[:80], "evidence": str(n.get("evidence"))[:120], "span": list(span)})
    for c in raw.get("claims") or []:
        span = locate(c.get("evidence", ""), chapter_text)
        if span is None or c.get("type") not in entity_contracts.CLAIM_TYPES:
            dropped.append({"chapter": chapter, "what": "claim", "type": c.get("type"), "why": "证据不在本章"})
            continue
        claims.append({"chapter": chapter, "type": c["type"], "subject": str(c.get("subject") or "").strip(), "object": str(c.get("object") or "").strip(),
                       "scope": c.get("scope") or "reality", "hidden_from_reader": bool(c.get("hidden_from_reader")),
                       "evidence": str(c.get("evidence"))[:160], "span": list(span)})
    return mentions, news, claims, dropped


def retrieval_keys(name: str) -> set[str]:
    """Pieces of a name likely to stand alone in the text - the parts around ·, the name without its title, the
    first and last two characters of a Chinese name.  Retrieval only: they decide who is *offered*, never who is
    meant (the bible says 莱恩·格雷 and 薇奥拉公主; the chapters say 莱恩 and 薇奥拉)."""
    keys: set[str] = set()
    for part in re.split(r"[·・.\s]+", name):
        for title in entity_contracts.TITLES:
            if len(part) > len(title) + 1 and part.endswith(title):
                part = part[: -len(title)]
                break
        if len(part) >= 2:
            keys.add(part)
            if len(part) >= 3 and re.fullmatch(r"[一-鿿]+", part):
                keys.update({part[:2], part[-2:]})
    return keys or {name}


def offered(entities: list[dict], forms: dict[str, set[str]], chapter_text: str, recent: set[str], cap: int = 80) -> list[dict]:
    """Candidates worth showing: any whose proper form or name piece occurs in the chapter, those on stage lately,
    the leads always - recall only, capped by how often they are named; the model decides who is meant."""
    scored: dict[str, int] = {}
    for e in entities:
        if e["status"] != "active":
            continue
        keys = set(forms.get(e["id"], ())) | retrieval_keys(e["canonical"])
        hits = sum(chapter_text.count(k) for k in keys if len(k) >= 2)
        if hits or e["id"] in recent or "主角" in str(e.get("role", "")):
            scored[e["id"]] = hits
    keep = {eid for eid, _ in sorted(scored.items(), key=lambda kv: -kv[1])[:cap]}
    return [e for e in entities if e["id"] in keep]


def extract_prompt(candidates: list[dict], forms: dict[str, set[str]], chapter_text: str) -> str:
    book = "\n".join(f"[{e['id']}] {e['canonical']}" + (f"（{e['role']}）" if e.get("role") else "")
                     + (f" 写法：{'、'.join(sorted(forms.get(e['id'], set()) - {e['canonical']})[:8])}" if len(forms.get(e["id"], set())) > 1 else "")
                     for e in candidates)
    return f"{entity_contracts.EXTRACT_RULES}\n\n人物账本：\n{book or '（空）'}\n\n本章原文：\n{chapter_text[:12000]}"


def shares_a_piece(form: str, e: dict, forms: dict[str, set[str]]) -> bool:
    keys = {k for k in retrieval_keys(e["canonical"]) | set(forms.get(e["id"], ())) if len(k) >= 2 and k not in entity_contracts.WEAK_KEYS}
    return any(k in form or form in k for k in keys)


def generic_name(name: str) -> bool:
    """A label rather than a name: 六秘环师, 那位女士, 怪物, 警员 - never the survivor of a merge, never merged into
    automatically (a rank absorbs a person, and then everyone of that rank becomes him)."""
    titled = next((t for t in sorted(entity_contracts.WEAK_KEYS, key=len, reverse=True) if name.endswith(t) and len(name) > len(t)), "")
    short = "·" not in name and (len(name) <= 2 or (len(name) == 3 and re.search(r"[女男人师猫狗声魂神鬼仆兵王主客者员]", name)))
    return bool(short or name[:1] in "那这某" or (titled and len(name) - len(titled) <= 2) or re.search(r"(秘环师|教士|警官|管家|女仆|仆人|侍女|士兵|护卫|保镖|老人|老者|青年|少年|少女|男人|女人|怪物|巨人)$", name))


def describe_record(e: dict) -> str:
    where = "故事圣经" if e.get("source") == "bible" else f"第 {e['source'][2:]} 章新出现"
    bits = [f"{e['canonical']}（{where}）"]
    if e.get("role"):
        bits.append(f"身份：{e['role']}")
    if e.get("description"):
        bits.append(f"描述：{e['description']}")
    if e.get("evidence"):
        bits.append(f"原文：{e['evidence']}")
    return "；".join(bits)


def lookalike_pairs(new: dict, entities: list[dict], forms: dict[str, set[str]], skip: set[str], limit: int = 3) -> list[dict]:
    """Records whose name shares a real piece with the new one (not a bare title or 女人) - earlier records only,
    so that two records created in the same chapter are compared once, from the later one's side."""
    keys = {k for k in retrieval_keys(new["canonical"]) | {new["canonical"]} if k not in entity_contracts.WEAK_KEYS}
    scored = []
    for e in entities:
        if e["status"] != "active" or e["id"] in skip or _number(e["id"]) >= _number(new["id"]):
            continue
        theirs = {k for k in retrieval_keys(e["canonical"]) | set(forms.get(e["id"], ())) if k not in entity_contracts.WEAK_KEYS}
        overlap = {k for k in keys & theirs if len(k) >= 2} | {k for k in keys if any(len(k) >= 2 and k in t for t in theirs)} \
            | {t for t in theirs if any(len(t) >= 2 and t in k for k in keys)}
        if overlap:
            scored.append((max(len(k) for k in overlap), e))
    return [e for _, e in sorted(scored, key=lambda x: -x[0])[:limit]]


def bridge_pairs(form: str, eid: str, entities: list[dict], forms: dict[str, set[str]]) -> list[dict]:
    """A written form that carries a distinct piece of two different records' names - 艾琳娜·路易斯 when the
    ledger has 艾琳娜 and 多洛茜·路易斯 - is the text saying they may be one person.  A piece both records share
    (格雷 in 莱恩·格雷 and 奥斯文·格雷) is not a bridge."""
    def pieces(e: dict) -> set[str]:
        return {k for k in retrieval_keys(e["canonical"]) | set(forms.get(e["id"], ())) if len(k) >= 2 and k not in entity_contracts.WEAK_KEYS and k in form}

    mine = next((pieces(e) for e in entities if e["id"] == eid), set())
    if not mine:
        return []
    others = []
    for e in entities:
        if e["status"] != "active" or e["id"] == eid:
            continue
        theirs = pieces(e)
        if theirs and (theirs - mine) and (mine - theirs):
            others.append(e)
    return others[:2]


def _number(eid: str) -> int:
    return int(eid[1:]) if eid[1:].isdigit() else 10 ** 9
