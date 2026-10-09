"""ledger_resolution_thin responsibilities; existing evidence and identity policy."""
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import re
import time
import novel_manga.application.identity.ledger_extraction as ledger_extraction
import novel_manga.application.identity.ledger_judges as ledger_judges
import novel_manga.application.identity.ledger_store as ledger_store
import novel_manga.entities.contracts as entity_contracts
import novel_manga.entities.evidence as entity_evidence
from novel_manga.application.identity import semantic_review

def require_judgments(answers):
    if any(answer.get('error') for answer in answers):
        raise RuntimeError('identity judge request failed; this chapter was not committed')


def generic_records(ledger: "Ledger") -> list[dict]:
    """Bible records that are not proper names - 那位女士, 秘女, 灵魂, 调查师, 白猫: the book's cast list was built by
    name scans and holds the same being under several such labels."""
    out = []
    for e in ledger.entities:
        if e["status"] != "active" or e.get("source") != "bible":
            continue
        name = e["canonical"]
        if entity_evidence.generic_record(e):
            out.append(e)
    return out


def dedup_generic(ledger: "Ledger", workers: int = 4) -> list[dict]:
    """One question per generic bible record: which of the other generic records is the same being?  The bible's
    descriptions are the card builder's inventions, not the book's words, so nothing merges on them: every pair
    the model names is a pending claim for a person (both sides naming each other is noted in the verdict)."""
    rows = generic_records(ledger)
    if len(rows) < 2:
        return []


    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        answers = list(pool.map(lambda e: ledger_judges.judge_generic(e, rows), rows))
    require_judgments(answers)
    votes: dict[str, list[str]] = {}
    for e, answer in zip(rows, answers):
        for other in answer.get("same_ids") or []:
            if other in ledger.by_id and other != e["id"]:
                votes.setdefault("|".join(sorted((e["id"], other), key=entity_evidence._number)), []).append(str(answer.get("why") or "")[:120])
    made = []
    with ledger.lock:
        for key, whys in votes.items():
            a, b = key.split("|")
            if ledger.canonical(a) == ledger.canonical(b):
                continue
            both = len(whys) >= 2  # each side named the other
            row = {"chapter": 0, "type": "same_as", "subject": b, "object": a, "scope": "reality", "hidden_from_reader": False,
                   "evidence": f"圣经描述：{ledger.by_id[a].get('description', '')} / {ledger.by_id[b].get('description', '')}", "span": [],
                   "source": "dedup", "id": f"c0000-{len(made) + 1:02d}", "resolved": True, "verdict": "supports" if both else "insufficient",
                   "why": ("双方互认；" if both else "") + whys[0], "status": "pending"}
            ledger.claims = [c for c in ledger.claims if c["id"] != row["id"]]
            _settle(ledger, row, 0)
            made.append(row)
    ledger.save()
    return made


def evidence_pack(ledger: "Ledger", a: str, b: str, claim_type: str, limit: int = 6,
                  *, chapters: list[int] | None = None) -> str:
    """Everything the book has said about two records, for one settling question."""
    chapters = ledger.chapters_read() if chapters is None else chapters
    on_stage: dict[str, set[int]] = {a: set(), b: set()}
    quotes: dict[str, list[str]] = {a: [], b: []}
    for c in chapters:
        for m in ledger.mentions_by_chapter.get(c, []):
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
    for r in ledger.relations_through(10 ** 9):
        if {r["from"], r["to"]} & {a, b} and len(relations) < 8:
            relations.append(f"第{r['chapter']}章 {ledger.name_of(r['from'])}→{ledger.name_of(r['to'])}：{entity_contracts.RELATION_BASES.get(r['base'], r['base'])}/{entity_contracts.RELATION_STANCES.get(r['stance'], r['stance'])}"
                             + (f"，称呼「{r['address']}」" if r.get("address") else ""))

    def side(eid: str) -> str:
        e = ledger.by_id[eid]
        chs = sorted(on_stage[eid])
        return (f"{entity_evidence.describe_record(e)}；已见写法：{'、'.join(sorted(ledger.forms.get(eid, {e['canonical']}))[:10])}；"
                f"在场 {len(chs)} 章" + (f"（{chs[0]}–{chs[-1]}）" if chs else "") + "\n  原句：" + (" / ".join(quotes[eid]) or "无"))

    labels = [ledger.name_of(x) for x in (a, b) if entity_evidence.generic_record(ledger.by_id[x])]
    return (f"关系：{ledger.name_of(a)} {claim_type} {ledger.name_of(b)}\n记录甲：{side(a)}\n记录乙：{side(b)}\n"
            f"两人同时在场的章：{together[:12] if together else '没有'}\n"
            + "相关证据句：\n" + "\n".join(f"  第{c['chapter']}章（{c.get('source', 'text')}，{c.get('verdict')}）：{c['evidence']}" for c in related[:8])
            + ("\n人物关系记录：\n  " + "\n  ".join(relations) if relations else "")
            + (f"\n注意：「{'」「'.join(labels)}」是称呼或身份而不是名字。这种记录只有在全书里始终只指这一个人时（如“那位女士”后来揭晓就是某人）才能算同一人；"
               "若它在书里指过不止一个人（女仆、秘女、护卫这类），就判不成立。" if labels else "")
            + ("\n注意：这是换身体/化身类关系，两人同时被提到不构成反证——灵魂和身体的主人本来就会一起出现；要看的是原文有没有明说谁在谁的身体里。"
               if claim_type in entity_contracts.BODY_TYPES else ""))


def settle_pending(ledger: "Ledger", workers: int = 4) -> list[dict]:
    """Every pending claim between two records is judged once more with the whole book's evidence, by two
    judges asked in opposite directions.  Both supports -> accepted (a same_as merges, reversibly); both
    contradicts -> rejected; anything else stays pending with the two answers noted.  A person's standing
    decision (corrections.json) always wins and is never re-asked."""
    todo = [c for c in ledger.claims if c.get("status") == "pending" and c["subject"] in ledger.by_id and c["object"] in ledger.by_id
            and not _correction_for(ledger, c) and ledger.canonical(c["subject"]) != ledger.canonical(c["object"])]
    seen = set()
    pairs = []
    for c in todo:
        key = (c['type'], relationship_pair(c, ledger.canonical))
        if key in seen:
            continue
        seen.add(key)
        pairs.append(c)
    index = ledger.entity_chapters()
    packs = []
    related_claims = []
    for c in pairs:
        a, b = ledger.canonical(c['subject']), ledger.canonical(c['object'])
        related_claims.append([r for r in ledger.claims if r['type'] == c['type']
                              and relationship_pair(r, ledger.canonical) == relationship_pair(c, ledger.canonical)])
        chapters = sorted(set(index.get(a, [])) | set(index.get(b, [])))
        packs.append(evidence_pack(ledger, a, b, c['type'], chapters=chapters))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        first = list(pool.map(lambda p: ledger_judges.judge_settle(p, entity_contracts.SETTLE_RULES), packs))
        second = list(pool.map(lambda p: ledger_judges.judge_settle(p, entity_contracts.SETTLE_RULES_B), packs))
        if any(answer.get('error') for answer in [*first, *second]):
            raise RuntimeError('settle judge request failed; ledger decisions were not changed')
        # No human queue (2026-09-14): a split vote goes to a third judge who must decide, and a body claim whose
        # quotations never name the body's owner gets one targeted question instead of waiting for a person.
        votes = [(x.get("verdict", "insufficient"), y.get("verdict", "insufficient")) for x, y in zip(first, second)]
        split = [i for i, v in enumerate(votes) if v not in (("supports", "supports"), ("contradicts", "contradicts"))]
        def tiebreak(i):
            prior = f"\n\n第一审：{first[i].get('verdict')}，理由：{first[i].get('why', '')}\n第二审：{second[i].get('verdict')}，理由：{second[i].get('why', '')}\n请针对这两份理由的实质分歧，按原文确认所问关系。"
            return ledger_judges.judge_settle(packs[i] + prior, entity_contracts.SETTLE_RULES_TIEBREAK)
        third = dict(zip(split, pool.map(tiebreak, split)))
        if any(answer.get('error') for answer in third.values()):
            raise RuntimeError('settle tiebreak request failed; ledger decisions were not changed')

        def owner_named(i: int) -> bool:
            c = pairs[i]
            a, b = ledger.canonical(c["subject"]), ledger.canonical(c["object"])
            twins = related_claims[i]
            return any(_evidence_names(ledger, b, r["evidence"]) for r in twins)

        body_check = [i for i in range(len(pairs)) if pairs[i]["type"] in entity_contracts.BODY_TYPES
                      and (votes[i] == ("supports", "supports") or third.get(i, {}).get("verdict") == "supports") and not owner_named(i)]
        # A body claim whose quotations never name the body's owner is rejected outright - no judge decides it from
        # context: 2026-09-14 the targeted question accepted 莱恩 occupies_body 奥斯文 from "占据了新身体" (a new body,
        # not 奥斯文's), and for five hours every rebuilt clip drew the lead with the old man's card.
        fourth = {i: {"verdict": "contradicts", "why": "证据句没点到身体主人的名字，按不成立处理"} for i in body_check}
    settled = []
    with ledger.lock:
        for i, (c, x, y) in enumerate(zip(pairs, first, second)):
            vote = votes[i]
            note = f"全书复判：{vote[0]}/{vote[1]}；{str(x.get('why') or '')[:80]} | {str(y.get('why') or '')[:80]}"
            a, b = ledger.canonical(c["subject"]), ledger.canonical(c["object"])
            twins = related_claims[i]
            if vote == ("supports", "supports"):
                status = "accepted"
            elif vote == ("contradicts", "contradicts"):
                status = "rejected"
            else:
                t = third.get(i, {})
                status = "accepted" if t.get("verdict") == "supports" else "rejected"
                note += f"；三审定 {t.get('verdict')}：{str(t.get('why') or '')[:80]}"
            if i in fourth:
                f = fourth[i]
                status = "accepted" if f.get("verdict") == "supports" else "rejected"
                note = f"换身体专项复核 {f.get('verdict')}：{str(f.get('why') or '')[:80]}；" + note
            if status == "accepted" and c["type"] == "same_as" \
                    and entity_contracts.looks_like_transformation(c.get("evidence", "")):
                # 变身/乔装类证据不直证同一人(那是 transformation):same_as 转人工,不自动合并。
                # 十日终焉许流年案(2026-09-30):"陡然变成了"被三审接受自动合并,同场章绑定全废。
                status, note = "pending", "证据是变身/乔装类描述,不直证同一人,转人工裁决；" + note
            carded = bool(ledger.by_id[a].get("asset_id")) and bool(ledger.by_id[b].get("asset_id"))
            lead = any("主角" in str(ledger.by_id[x].get("role", "")) for x in (a, b))
            for r in twins:
                r["settled"], r["status"] = note, status
                # what a person should look at: a body claim, a merge of two carded records (the picture changes
                # either way), or anything touching a lead; a label folding into an uncarded record changes no picture
                r["priority"] = c["type"] in entity_contracts.BODY_TYPES or (c["type"] in entity_contracts.MERGING and carded) or lead
            if status in ("accepted", "rejected", "pending"):
                # remembered apart from the model's output, so that a chapter read again does not undo it; a person's
                # later decision on the same pair replaces it
                ledger.corrections = [k for k in ledger.corrections if not (k["type"] == c["type"] and relationship_pair(k) == relationship_pair(c))]
                ledger.corrections.append({"claim": c["id"], "type": c["type"], "subject": c['subject'], "object": c['object'], "decision": status, "note": note[:160],
                                           "by": "settle", "at": time.strftime("%Y-%m-%d %H:%M")})
            if status == "accepted" and c["type"] in entity_contracts.MERGING:
                _merge(ledger, c["subject"], c["object"], c["id"], c["chapter"])
            settled.append({"id": c["id"], "type": c["type"], "subject": ledger.name_of(c["subject"]), "object": ledger.name_of(c["object"]),
                            "status": status, "votes": vote, "why": note})
    settled.extend(close_leftovers(ledger))
    ledger.save()
    return settled


def close_leftovers(ledger: "Ledger") -> list[dict]:
    """Close valid one-sided events; unresolved bilateral identities remain pending."""
    closed = []
    with ledger.lock:
        for c in ledger.claims:
            if c.get("status") != "pending":
                continue
            s, o = c.get("subject"), c.get("object")
            if s in ledger.by_id and o in ledger.by_id and ledger.canonical(s) == ledger.canonical(o):
                c["status"], c["settled"] = "accepted", "两条记录已经合并，判断自然成立；自动结案"
            elif c['type'] in entity_contracts.ONE_SIDED and s in ledger.by_id and o not in ledger.by_id:
                c["status"], c["settled"] = "closed", "单边事件记录，未确证身份合并"
            else:
                continue
            closed.append({"id": c.get("id"), "type": c["type"], "subject": ledger.name_of(s) if s in ledger.by_id else s,
                           "object": ledger.name_of(o) if o in ledger.by_id else o, "status": c["status"], "votes": None, "why": c["settled"]})
    return closed


def resolve_chapter(ledger, chapter: int, text: str, raw: dict | None = None, workers: int = 4, texts=None) -> dict | None:
    # Metadata is small; historical chapter rows remain shared and are never mutated here.
    fields = ('entities', 'claims', 'merges', 'dropped', 'corrections', 'asked',
              'by_id', 'by_name', 'forms', 'recent')
    before = deepcopy({name: getattr(ledger, name) for name in fields})
    chapters = {name: dict(getattr(ledger, name)) for name in ('mentions_by_chapter', 'relations_by_chapter')}
    try:
        raw = raw if raw is not None else ledger_store.read_json(ledger.raw_path(chapter), None)
        if not raw or raw.get('error'):
            return None
        for attempt in range(2):
            if attempt:
                for name, value in deepcopy(before).items(): setattr(ledger, name, value)
                for name, value in chapters.items(): setattr(ledger, name, dict(value))
            summary, raw = resolve_candidate(ledger, chapter, text, raw, workers, texts=texts)
            raw, review = semantic_review.book(ledger, chapter, text, raw, texts=texts,
                                               original_records=before['entities'])
            if review['approved']:
                raw['semantic_review'] = review
                ledger_store.write_json(ledger.mentions_path(chapter), ledger.mentions_by_chapter[chapter])
                ledger_store.write_json(ledger.relations_path(chapter), ledger.relations_by_chapter[chapter])
                ledger.save()
                ledger_store.write_json(ledger.raw_path(chapter), raw)
                return summary
        raise ValueError('identity correction did not pass final semantic review: ' + review.get('reason', ''))
    except Exception:
        for name, value in {**before, **chapters}.items():
            setattr(ledger, name, value)
        raise


def resolve_candidate(ledger, chapter: int, text: str, raw: dict | None = None, workers: int = 4, texts=None) -> dict | None:
    """In chapter order: mentions get entity ids (new people get records), claims get verdicts and a status,
    supported same_as claims become merge edges.  Re-running a chapter replaces its mentions and claims."""
    raw = raw if raw is not None else ledger_store.read_json(ledger.raw_path(chapter), None)
    if raw is None or raw.get("error"):
        return None
    late = ledger_extraction.stale(ledger, raw, text)
    if late:  # read again, now that the people this chapter names are on the books
        # The chapter owner already holds the book lock; this helper only reads/calls the model.
        again = ledger_extraction.read_raw(ledger, chapter, text, force=True)
        if again.get('error'):
            raise RuntimeError('identity rereading request failed; this chapter was not committed')
        if not again.get("error"):
            again["fresh"] = True
            ledger_store.write_json(ledger.raw_path(chapter), again)
            raw = again
    mentions, news, claims, dropped = entity_evidence.ground(chapter, text, raw)
    chapter_aliases = (ledger_store.read_json(ledger.novel_dir / 'reading_cast.json', {}).get('chapter_aliases') or {}).get(str(chapter), {})
    confirmed = {form: ledger.canonical(ledger.by_name[name]['id']) for form, name in chapter_aliases.items()
                 if name in ledger.by_name}
    with ledger.lock:
        ledger.dropped.extend(dropped)
        local_new: dict[str, str] = dict(confirmed)
        created: list[dict] = []
        relinks: list[tuple[dict, dict, list[dict]]] = []  # (mention, assigned record, records the form's pieces name)
        for n in news:
            existing = ledger._resolve_ref(n['name'], local_new)
            if existing is not None:
                local_new[n["name"]] = existing
                continue
            created.append(ledger._add_entity(n["name"], n["kind"], n["named"], n["description"], chapter, n["evidence"], n["span"]))
            local_new[n["name"]] = created[-1]["id"]
        for m in mentions:
            eid = confirmed.get(m['form']) or ledger._resolve_ref(m["entity"], local_new)
            if eid is None and m["entity"].startswith("NEW:"):
                name = m["entity"][4:].strip() or m["form"]
                created.append(ledger._add_entity(name, "person", m["kind"] == "proper", "", chapter, m["evidence"], m["span"]))
                eid = created[-1]["id"]
                local_new[name] = eid
            claimed = re.sub(r"[（(].*$", "", m.pop("entity_name", "")).strip()  # the prompt shows 名字（身份）
            if m['form'] not in confirmed and eid and claimed and ledger.name_of(eid) != claimed and claimed not in ledger.forms.get(eid, ()):
                # the id and the name the model copied disagree: the name is the safer half of a slipped row
                other = ledger.by_name.get(claimed)
                ledger.dropped.append({"chapter": chapter, "what": "mention", "form": m["form"], "why": f"编号 {eid} 与名字 {claimed} 不符",
                                     "fixed": bool(other)})
                eid = ledger.canonical(other["id"]) if other else None
            m["entity"] = eid or "UNCERTAIN"
            if eid and m["kind"] == "proper" and eid not in local_new.values() and not entity_evidence.shares_a_piece(m["form"], ledger.by_id[eid], ledger.forms):
                # a proper form on a record it shares no name piece with: a nickname (齐娜 for 卡珊德拉婆婆) or a slip
                # (路易斯小姐 on 那位女士) - the model is asked once per form and record, with the sentence
                cached = ledger.asked.get(f"{m['form']}|{eid}")
                if cached is None:
                    options = [e for e in ledger.entities if e["status"] == "active" and e["id"] != eid and entity_evidence.shares_a_piece(m["form"], e, ledger.forms)][:4]
                    relinks.append((m, ledger.by_id[eid], options))
                elif cached.startswith("e") and cached in ledger.by_id:
                    m["entity"] = eid = ledger.canonical(cached)
                elif cached == "UNCERTAIN":
                    m["entity"], eid = "UNCERTAIN", None
            if eid and m["kind"] == "proper":
                ledger.forms.setdefault(eid, set()).add(m["form"])
            if eid and m["presence"] != "mentioned":
                ledger.recent[eid] = max(ledger.recent.get(eid, 0), chapter)
    if relinks:  # model calls outside the lock, then the mentions are corrected before anything is written
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            answers = list(pool.map(lambda r: ledger_judges.judge_link(r[0]["form"], r[0]["evidence"], r[1], r[2], ledger.forms), relinks))
        require_judgments(answers)
        with ledger.lock:
            for (m, assigned, options), answer in zip(relinks, answers):
                choice = str(answer.get("entity") or "UNCERTAIN").strip()
                ledger.asked[f"{m['form']}|{assigned['id']}"] = choice
                if choice == assigned["id"]:
                    continue
                ledger.forms.get(assigned["id"], set()).discard(m["form"])
                ledger.dropped.append({"chapter": chapter, "what": "mention", "form": m["form"], "why": f"改判 {assigned['canonical']} → {choice}: {str(answer.get('why') or '')[:60]}"})
                if choice.startswith("e") and choice in ledger.by_id:
                    m["entity"] = ledger.canonical(choice)
                    ledger.forms.setdefault(m["entity"], set()).add(m["form"])
                elif choice == "NEW":
                    created.append(ledger._add_entity(m["form"], "person", True, "", chapter, m["evidence"], m["span"]))
                    m["entity"] = created[-1]["id"]
                else:
                    m["entity"] = "UNCERTAIN"
    with ledger.lock:
        ledger.mentions_by_chapter[chapter] = mentions
        relations = []
        for r in entity_evidence.ground_relations(chapter, text, raw, ledger.dropped):
            a, b = ledger._resolve_ref(r["from"], local_new), ledger._resolve_ref(r["to"], local_new)
            if not a or not b or a == b:
                ledger.dropped.append({"chapter": chapter, "what": "relation", "why": f"两端归不到记录 {r['from']}→{r['to']}"})
                continue
            relations.append({**r, "from": a, "to": b})
        ledger.relations_by_chapter[chapter] = relations
        ledger.claims = [c for c in ledger.claims if c["chapter"] != chapter]
        pending = []
        for c in claims:
            for side_key in ("subject", "object"):  # a NEW:name the model used only inside a claim still gets a record
                ref = c[side_key]
                if ref.startswith("NEW:") and ledger._resolve_ref(ref, local_new) is None and ref[4:].strip():
                    created.append(ledger._add_entity(ref[4:].strip(), "person", True, "", chapter, c["evidence"], c["span"]))
                    local_new[ref[4:].strip()] = created[-1]["id"]
            s, o = ledger._resolve_ref(c["subject"], local_new), ledger._resolve_ref(c["object"], local_new)
            if c["type"] not in entity_contracts.ONE_SIDED and s and o and s == o:
                ledger.dropped.append({"chapter": chapter, "what": "claim", "type": c["type"], "why": f"自指 {ledger.name_of(s)}"})
                continue
            pending.append({**c, "id": f"c{chapter:04d}-{len(pending) + 1:02d}", "subject": s or c["subject"], "object": o or c["object"],
                            "resolved": bool(s and (o or c["type"] in entity_contracts.ONE_SIDED))})
        # a new record that shares a name piece with an older one (or with another new one): the read-ahead
        # may have extracted this chapter before the older record existed, so the model is asked with both in view
        questions = [(new, old, "") for new in created for old in entity_evidence.lookalike_pairs(new, ledger.entities, ledger.forms, set())]
        # a written form that bridges two records (艾琳娜·路易斯): asked once per pair, the answer remembered
        for m in mentions:
            if m["kind"] != "proper" or not m["entity"].startswith("e"):
                continue
            for other in entity_evidence.bridge_pairs(m["form"], m["entity"], ledger.entities, ledger.forms):
                key = "|".join(sorted((m["entity"], other["id"]), key=entity_evidence._number))
                if key in ledger.asked or any({q[0]["id"], q[1]["id"]} == {m["entity"], other["id"]} for q in questions):
                    continue
                questions.append((ledger.by_id[m["entity"]], other, m["evidence"]))
    if pending or questions:  # the model calls run outside the lock
        def side(ref: str) -> str:
            return entity_evidence.describe_record(ledger.by_id[ref]) if ref in ledger.by_id else ref

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            verdicts = list(pool.map(lambda r: ledger_judges.judge_claim(r, side(r["subject"]), side(r["object"])), pending))
            answers = list(pool.map(lambda q: ledger_judges.judge_same(q[0], q[1], ledger.forms.get(q[0]["id"], set()), ledger.forms.get(q[1]["id"], set()), q[2]),
                                    questions))
        require_judgments([*verdicts, *answers])
        with ledger.lock:
            for row, verdict in zip(pending, verdicts):
                row["verdict"], row["why"] = verdict.get("verdict", "insufficient"), str(verdict.get("why") or "")[:120]
                supported = row["verdict"] == "supports" and row["resolved"] and row["scope"] == "reality"
                if supported and row["type"] not in entity_contracts.ONE_SIDED and not all(_evidence_names(ledger, x, row["evidence"]) for x in (row["subject"], row["object"])):
                    # "占据了别人身体" says nothing about whose body: a sentence that does not name both
                    # records cannot settle a relation between them, whatever the judge made of it
                    supported, row["why"] = False, "证据句没有点到两端的名字；" + row["why"][:100]
                row["status"] = "pending" if row["hidden_from_reader"] or not supported else "accepted"
                _settle(ledger, row, chapter)
            for (new, old, note), answer in zip(questions, answers):
                same = answer.get("same", "unsure")
                ledger.asked["|".join(sorted((new["id"], old["id"]), key=entity_evidence._number))] = same
                if same == "different" or ledger.canonical(new["id"]) == ledger.canonical(old["id"]):
                    continue
                row = {"chapter": chapter, "type": "same_as", "subject": new["id"], "object": old["id"], "scope": "reality",
                       "hidden_from_reader": False, "evidence": note or new.get("evidence", ""), "span": new.get("span", []),
                       "source": "bridge" if note else "reconcile", "id": f"c{chapter:04d}-{len(pending) + 1:02d}", "resolved": True,
                       "verdict": "supports" if same == "same" else "insufficient", "why": str(answer.get("why") or "")[:120],
                       "status": "accepted" if same == "same" else "pending"}
                pending.append(row)
                _settle(ledger, row, chapter)
    merged = [f"{ledger.name_of(m['from'])}→{ledger.name_of(m['into'])}" for m in ledger.merges if m["chapter"] == chapter]
    return {"chapter": chapter, "mentions": len(mentions), "new": [e["canonical"] for e in created if e["status"] == "active"],
            "claims": Counter(r["status"] for r in pending), "merged": merged, "relations": len(relations), "dropped": len(dropped),
            "reread": [e["canonical"] for e in late]}, raw


def _evidence_names(ledger, ref: str, evidence: str) -> bool:
    """Does the quotation carry a piece of this record's name (or a form it has been called by)?"""
    if ref not in ledger.by_id:
        return True  # free text, e.g. what someone transformed into
    e = ledger.by_id[ref]
    keys = {k for k in entity_evidence.retrieval_keys(e["canonical"]) | ledger.forms.get(e["id"], set()) if len(k) >= 2 and k not in entity_contracts.WEAK_KEYS}
    return any(k in evidence for k in keys)


def _correction_for(ledger, row: dict) -> dict | None:
    """The person's standing decision on this relation, if any: matched by type and the two records (either
    order for the symmetric types), not by claim id, so it survives a chapter being read again."""
    raw_pair = relationship_pair(row)
    for c in ledger.corrections:
        if c['type'] == row['type'] and relationship_pair(c) == raw_pair:
            return c
    pair = relationship_pair(row, ledger.canonical)
    if ledger.canonical(row['subject']) == ledger.canonical(row['object']):
        return None  # a collapsed group does not identify which independent decision was made
    for c in ledger.corrections:
        if c["type"] == row["type"] and relationship_pair(c, ledger.canonical) == pair:
            return c
    return None


def relationship_pair(row, resolve=lambda ref: ref):
    pair = (resolve(row['subject']), resolve(row['object']))
    return frozenset(pair) if row['type'] in {'same_as', 'lookalike'} else pair


def _settle(ledger, row: dict, chapter: int) -> None:
    """Apply a standing correction, then act on the status: an accepted same_as merges; the row is recorded.
    A merge that would fold a person into a label (杰克·德昂 into 六秘环师) waits for a person instead."""
    correction = _correction_for(ledger, row)
    if correction:
        row["status"], row["corrected"] = correction["decision"], correction.get("note", "")
    elif (row["status"] == "accepted" and row["type"] in entity_contracts.MERGING
          and any(entity_evidence.generic_record(ledger.by_id[x]) and ledger.by_id[x].get("source") == "bible" for x in (row["subject"], row["object"]) if x in ledger.by_id)):
        # a label the bible carries as a character (六秘环师, 那位女士) may name several people over the book;
        # a label the ledger itself introduced in one chapter (作家小姐) is that chapter's person and may merge
        row["status"], row["why"] = "pending", "一方是圣经里的泛称记录，合并需人工确认；" + str(row.get("why") or "")[:100]
    if row["status"] == "accepted" and row["type"] in entity_contracts.MERGING and row["subject"] != row["object"]:
        _merge(ledger, row["subject"], row["object"], row["id"], chapter)
    ledger.claims.append(row)


def decide(ledger, claim_id: str, decision: str, note: str = "") -> bool:
    """A person accepts or rejects a claim.  The decision is stored apart (corrections.json) and applied
    now: accepting a same_as merges, rejecting one whose merge went through undoes that merge."""
    row = next((c for c in ledger.claims if c["id"] == claim_id), None)
    if row is None or decision not in ("accepted", "rejected"):
        return False
    with ledger.lock:
        pair = relationship_pair(row)
        if decision == 'rejected' and row['type'] == 'same_as':
            connected = {row['subject']}
            proofs = [c for c in ledger.claims if c['type'] == 'same_as' and c.get('status') == 'accepted'
                      and relationship_pair(c) != pair and c['subject'] in ledger.by_id and c['object'] in ledger.by_id]
            while True:
                expanded = connected | {eid for c in proofs if {c['subject'], c['object']} & connected for eid in (c['subject'], c['object'])}
                if expanded == connected:
                    break
                connected = expanded
            if row['object'] in connected:
                raise ValueError('other accepted identity proofs still join this pair; resolve the conflicting proof first')
        ledger.corrections = [c for c in ledger.corrections if c.get('claim') != claim_id
                            and not (c['type'] == row['type'] and relationship_pair(c) == pair)]
        ledger.corrections.append({"claim": claim_id, "type": row["type"], "subject": row["subject"], "object": row["object"],
                                 "decision": decision, "note": note, "by": "human", "at": time.strftime("%Y-%m-%d %H:%M")})
        same_claims = [c for c in ledger.claims if c['type'] == row['type'] and relationship_pair(c) == pair]
        for c in same_claims:
            c['status'], c['corrected'] = decision, note
        ids = {c['id'] for c in same_claims}
        merge_claims = [m['claim'] for m in ledger.merges if m['claim'] in ids]
        merged = bool(merge_claims)
        if decision == "accepted" and row["type"] in entity_contracts.MERGING and not merged and row["subject"] != row["object"]:
            _merge(ledger, row["subject"], row["object"], claim_id, row["chapter"])
    if decision == "rejected" and merged:
        for owner in merge_claims:
            undo_merge(ledger, owner)
    ledger.save()
    return True


def _merge(ledger, a: str, b: str, claim_id: str, chapter: int) -> None:
    a, b = ledger.canonical(a), ledger.canonical(b)
    keep, drop = sorted((a, b), key=entity_evidence._number)  # the earlier record survives; the bible's ids come first...
    if entity_evidence.generic_record(ledger.by_id[keep]) and not entity_evidence.generic_record(ledger.by_id[drop]):
        keep, drop = drop, keep  # ...unless the earlier one is a label and the later one a name
    if ledger.by_id[drop]["status"] != "active" or ledger.canonical(keep) == ledger.canonical(drop):
        return
    ledger.by_id[drop]["status"], ledger.by_id[drop]["merged_into"] = "merged", keep
    ledger.forms.setdefault(keep, set()).update(ledger.forms.pop(drop, set()))
    ledger.by_name[ledger.by_id[drop]["canonical"]] = ledger.by_id[keep]
    ledger.merges.append({"claim": claim_id, "from": drop, "into": keep, "chapter": chapter})


def undo_merge(ledger, claim_id: str) -> bool:
    """Remove one proof and replay surviving proofs in its affected identity component."""
    with ledger.lock:
        row = next((m for m in ledger.merges if m["claim"] == claim_id), None)
        if row is None:
            return False
        claims = {c['id']: c for c in ledger.claims}
        component = {row['from'], row['into']}
        def endpoints(edge):
            claim = claims.get(edge['claim'], {})
            return {edge['from'], edge['into'], claim.get('subject'), claim.get('object')} & ledger.by_id.keys()
        while True:
            expanded = component | {eid for edge in ledger.merges if endpoints(edge) & component for eid in endpoints(edge)}
            if expanded == component:
                break
            component = expanded
        affected = [m for m in ledger.merges if endpoints(m) & component]
        owners = {m['claim'] for m in affected}
        redundant = [c for c in ledger.claims if c['type'] == 'same_as' and c.get('status') == 'accepted'
                     and c['id'] not in owners and c['subject'] in component and c['object'] in component]
        ledger.merges = [m for m in ledger.merges if m not in affected]
        for eid in component:
            ledger.by_id[eid].update(status='active', merged_into=None)
        for c in ledger.claims:
            if c["id"] == claim_id:
                c["status"] = "rejected"
        ledger.rebuild_identity_views()
        for edge in affected:
            if edge['claim'] == claim_id:
                continue
            claim = claims.get(edge['claim'], {})
            _merge(ledger, claim.get('subject', edge['from']), claim.get('object', edge['into']), edge['claim'], edge['chapter'])
        for claim in redundant:
            _merge(ledger, claim['subject'], claim['object'], claim['id'], claim['chapter'])
        ledger.rebuild_identity_views()
    ledger.save()
    return True
