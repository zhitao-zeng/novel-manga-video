"""Frozen pre-index evidence pack, solely for before/after regression replay."""
from novel_manga.application.identity import ledger_store
from novel_manga.entities import evidence as entity_evidence, contracts as entity_contracts

def full_scan_evidence_pack(ledger: "Ledger", a: str, b: str, claim_type: str, limit: int = 6) -> str:
    """Everything the book has said about two records, for one settling question."""
    chapters = sorted(int(p.stem[3:]) for p in (ledger.base / 'mentions').glob('ch_*.json')
                      if p.stem[3:].isdigit())
    on_stage: dict[str, set[int]] = {a: set(), b: set()}
    quotes: dict[str, list[str]] = {a: [], b: []}
    for c in chapters:
        for m in ledger_store.read_json(ledger.mentions_path(c), []):
            if not str(m.get("entity", "")).startswith("e"):
                continue
            eid = ledger.canonical(m["entity"])
            if eid not in on_stage:
                continue
            if m.get("presence") == "on_stage":
                on_stage[eid].add(c)
            if m.get("kind") == "proper" and len(quotes[eid]) < limit and m.get("evidence"):
                quotes[eid].append(f"第{c}章：{m['evidence']}")
    together = sorted(on_stage[a] & on_stage[b])
    related = [c for c in ledger.claims if {ledger.canonical(c["subject"]), ledger.canonical(c["object"])} == {a, b}]
    relations = []
    for r in full_scan_relations(ledger, 10 ** 9):
        if {r["from"], r["to"]} & {a, b} and len(relations) < 8:
            relations.append(f"第{r['chapter']}章 {ledger.name_of(r['from'])}→{ledger.name_of(r['to'])}：{entity_contracts.RELATION_BASES.get(r['base'], r['base'])}/{entity_contracts.RELATION_STANCES.get(r['stance'], r['stance'])}"
                             + (f"，称呼「{r['address']}」" if r.get("address") else ""))

    def side(eid: str) -> str:
        e = ledger.by_id[eid]
        chs = sorted(on_stage[eid])
        return (f"{entity_evidence.describe_record(e)}；已见写法：{'、'.join(sorted(ledger.forms.get(eid, {e['canonical']}))[:10])}；"
                f"在场 {len(chs)} 章" + (f"（{chs[0]}–{chs[-1]}）" if chs else "") + "\n  原句：" + (" / ".join(quotes[eid]) or "无"))

    labels = [ledger.name_of(x) for x in (a, b) if entity_evidence.generic_name(ledger.name_of(x)) and ledger.by_id[x].get("source") == "bible"]
    return (f"关系：{ledger.name_of(a)} {claim_type} {ledger.name_of(b)}\n记录甲：{side(a)}\n记录乙：{side(b)}\n"
            f"两人同时在场的章：{together[:12] if together else '没有'}\n"
            + "相关证据句：\n" + "\n".join(f"  第{c['chapter']}章（{c.get('source', 'text')}，{c.get('verdict')}）：{c['evidence']}" for c in related[:8])
            + ("\n人物关系记录：\n  " + "\n  ".join(relations) if relations else "")
            + (f"\n注意：「{'」「'.join(labels)}」是称呼或身份而不是名字。这种记录只有在全书里始终只指这一个人时（如“那位女士”后来揭晓就是某人）才能算同一人；"
               "若它在书里指过不止一个人（女仆、秘女、护卫这类），就判不成立。" if labels else "")
            + ("\n注意：这是换身体/化身类关系，两人同时被提到不构成反证——灵魂和身体的主人本来就会一起出现；要看的是原文有没有明说谁在谁的身体里。"
               if claim_type in entity_contracts.BODY_TYPES else ""))


def full_scan_relations(ledger, chapter: int) -> list[dict]:
    """Every relation row read so far up to and including this chapter, endpoints canonical."""
    rows = []
    for c in sorted(int(p.stem[3:]) for p in (ledger.base / 'mentions').glob('ch_*.json') if p.stem[3:].isdigit()):
        if c > chapter:
            break
        for r in ledger_store.read_json(ledger.relations_path(c), []):
            rows.append({**r, "from": ledger.canonical(r["from"]), "to": ledger.canonical(r["to"])})
    return rows
