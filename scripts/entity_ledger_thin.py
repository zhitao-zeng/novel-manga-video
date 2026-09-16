#!/usr/bin/env python
"""A ledger of who is who, kept the way an editor would keep it: every mention on record where it happened,
every identity claim with its quotation and a verdict, every merge reversible, one casting snapshot per scene.

The reading itself is a stage of story_pass_thin.py (parallel extraction ahead, ordered resolution under
the commit), which then grows the bible from this ledger instead of its own name scan.  This module is the
library plus a few views:

    entity_ledger_thin.py extract  --novel-dir X --chapters 1-50 [--workers 5]   model reads chapters -> entity/raw/
    entity_ledger_thin.py resolve  --novel-dir X --chapters 1-50                 ordered: mentions -> entities, claims -> verdicts
    entity_ledger_thin.py snapshot --novel-dir X --chapter N [--segments seg_4,seg_5]
    entity_ledger_thin.py index    --novel-dir X [--out entity_index.json]      the planner/packer/reviewer view
    entity_ledger_thin.py pending  --novel-dir X [--html]                        claims a person must confirm
    entity_ledger_thin.py undo     --novel-dir X --claim c0007-01                reverse one merge

Why not an alias table, short-form rules or global "forms" counts: 调查师 names 莱恩 in one chapter and
another investigator later; 路易斯 is either 艾蕾娅's alias or a different woman, and the picture depends on
which.  So a mention is recorded where it happened (chapter, quotation, character span, whether the form is
a proper name or a stand-in like "那女人", and whether the person is on stage, a voice, or only spoken of); a
relation between two records is a claim of one strict type - same_as, impersonates, lookalike, avatar_of,
occupies_body, reveal, transformation, death, return, rename - with a quotation the code locates in the
chapter and a second model call judges (supports / contradicts / insufficient); only a supported same_as
merges, and a merge is an edge (merged_into), never a deletion; claims the text keeps from the reader wait
for a person.  A scene's casting snapshot answers the storyboard's real question: which bodies may appear,
who acts through each, which names may be spoken, who is a voice only, what the audience must not learn yet.
Files live under <novel>/entity/.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "src")]
os.environ.setdefault("SECOND_REVIEW_JUDGE", "local")
import second_review  # noqa: E402,F401  (registers the local judge endpoints ask_json uses)
from novel_manga.model_client import ask_json  # noqa: E402

LEDGER_POLICY = "entity-ledger-v2"
RECENT_WINDOW = 15  # chapters: someone on stage this recently is offered even if the chapter never names them
CLAIM_TYPES = ["same_as", "impersonates", "lookalike", "avatar_of", "occupies_body", "reveal", "transformation", "death", "return", "rename"]
MERGING = {"same_as"}  # the only claim that joins two records
ONE_SIDED = {"death", "return", "transformation"}  # claims whose object may be free text
BODY_TYPES = {"occupies_body", "avatar_of", "transformation"}  # claims that change whose card the picture is drawn from
SCOPES = ["reality", "dream", "flashback", "hearsay", "hypothetical"]
PRESENCE = ["on_stage", "voice", "mentioned"]
PRESENCE_RANK = {"on_stage": 2, "voice": 1, "mentioned": 0}
# The relation vocabulary is graph-every-novel's (MIT): a durable structural base and a current stance, kept
# apart, because "her brother" stays true while "hostile" changes by the chapter.
RELATION_BASES = {
    "none": "无", "unknown": "未知", "kinship_parent_child": "亲子", "kinship_sibling": "兄弟姐妹", "kinship_spouse": "夫妻",
    "kinship_fiance": "婚约", "kinship_clan": "亲族", "kinship_adoptive": "收养", "same_faction": "同阵营", "same_unit": "同一小队",
    "friend": "朋友", "childhood_friend": "发小", "rival_pair": "对手", "household_family": "同一家", "opposing_faction": "敌对阵营",
    "superior_subordinate": "上下级", "lord_vassal": "君臣", "master_disciple": "师徒", "teacher_student": "师生",
    "commander_soldier": "将与兵", "employer_employee": "雇佣", "colleague": "同事", "classmate": "同学", "roommate": "室友",
    "contract_bound": "契约", "oath_bound": "誓约", "guardian_ward": "监护", "romantic_partner": "恋人", "temporary_alliance": "临时同盟",
}
RELATION_STANCES = {
    "unknown": "未知", "neutral": "中立", "friendly": "友好", "trusting": "信任", "protective": "保护", "dependent": "依赖", "admiring": "敬佩",
    "romantic_interest": "爱慕", "wary": "警惕", "distrustful": "怀疑", "hostile": "敌对", "fearful": "恐惧", "resentful": "怨恨",
    "jealous": "嫉妒", "obsessive": "执着", "submissive": "顺从", "dominant": "支配", "conflicted": "矛盾",
}

EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["mentions", "new_entities", "claims", "relations"],
    "properties": {
        "relations": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                 "required": ["from", "to", "base", "stance", "address", "hidden_from_reader", "evidence"],
                                                 "properties": {"from": {"type": "string"}, "to": {"type": "string"},
                                                                "base": {"type": "string", "enum": sorted(RELATION_BASES)},
                                                                "stance": {"type": "string", "enum": sorted(RELATION_STANCES)},
                                                                "address": {"type": "string"}, "hidden_from_reader": {"type": "boolean"},
                                                                "evidence": {"type": "string"}}}},
        "mentions": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                "required": ["form", "entity", "entity_name", "kind", "presence", "evidence"],
                                                "properties": {"form": {"type": "string"}, "entity": {"type": "string"}, "entity_name": {"type": "string"},
                                                               "kind": {"type": "string", "enum": ["proper", "contextual"]},
                                                               "presence": {"type": "string", "enum": PRESENCE},
                                                               "evidence": {"type": "string"}}}},
        "new_entities": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                    "required": ["name", "kind", "named", "description", "evidence"],
                                                    "properties": {"name": {"type": "string"},
                                                                   "kind": {"type": "string", "enum": ["person", "animal", "spirit", "other"]},
                                                                   "named": {"type": "boolean"}, "description": {"type": "string"},
                                                                   "evidence": {"type": "string"}}}},
        "claims": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                              "required": ["type", "subject", "object", "scope", "hidden_from_reader", "evidence"],
                                              "properties": {"type": {"type": "string", "enum": CLAIM_TYPES}, "subject": {"type": "string"},
                                                             "object": {"type": "string"}, "scope": {"type": "string", "enum": SCOPES},
                                                             "hidden_from_reader": {"type": "boolean"}, "evidence": {"type": "string"}}}},
    },
}
VERDICT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdict", "why"],
                  "properties": {"verdict": {"type": "string", "enum": ["supports", "contradicts", "insufficient"]}, "why": {"type": "string"}}}

EXTRACT_RULES = (
    "你在通读一部小说，手里有本章可能涉及的人物账本（编号、正式名、已见过的专指写法）。读本章原文，只输出 JSON。\n"
    "mentions：本章里指称人物的每一种写法各记一条（名字、姓氏、称谓、绰号、身份代称；不记代词他/她）。entity 填账本编号；"
    "账本按正式全名列人，原文常只写名或姓或去掉头衔（莱恩 就是账本里的 莱恩·格雷，薇奥拉 就是 薇奥拉公主），这种情况填账本编号、不要写 NEW；"
    "账本里确实没有的写 NEW:名字；本章无法确定指谁的写 UNCERTAIN。原文里有名有姓的人在账本里对不上任何一条时，宁写 NEW 也不要塞给"
    "一条泛称记录（吟游诗人、那位女士、调查师这类不是名字的条目）。entity_name 抄账本里该编号后面的正式名（NEW 或 UNCERTAIN 时留空），"
    "编号和名字必须是同一行的。kind：proper = 离开本章也能唯一指认此人的写法；"
    "contextual = 只在本段语境里才知道指谁的代称（那女人、医生、年轻人、教授）。presence：on_stage = 此人在场景里出现；"
    "voice = 只有声音（脑内声音、电话、门外、旁白）；mentioned = 只被提起、不在场。evidence 抄本章原文里含该写法的一小句，不超过 25 字。\n"
    "new_entities：账本没有、本章新出现的人物（或动物、灵体）；named 表示有真正的名字（“拉格特·富兰克林”是，“浓妆女人”“业务员”不是）；"
    "只提一次的无名路人不要写。\n"
    "claims：两条记录之间的身份关系，类型只能是：same_as（确是同一个人）、impersonates（subject 冒充/化名为 object）、"
    "lookalike（长得一样但不是同一人，如双胞胎）、avatar_of（subject 是 object 的分身/化身/投影）、occupies_body（subject 的灵魂在 object 的身体里）、"
    "reveal（本章揭晓 subject 就是 object）、transformation（subject 变成 object 所写的形态）、death、return、rename（subject 从此改叫 object）。"
    "scope：这件事在故事里是现实，还是梦境/回忆/传闻/假设。hidden_from_reader：原文此时是否仍对读者隐瞒这层关系。"
    "每条附本章原文里逐字的一句证据（不超过 40 字），抄不出来就不要写。没有就空数组。\n"
    "relations：本章能看出的两个人物之间的关系，from/to 填账本编号或 NEW:名字。base 是持久的结构关系："
    + "、".join(f"{k}={v}" for k, v in RELATION_BASES.items()) + "。stance 是 from 此刻对 to 的态度："
    + "、".join(f"{k}={v}" for k, v in RELATION_STANCES.items()) + "。address 是 from 在本章怎么称呼 to（叔叔、殿下、格雷先生），没叫过留空。"
    "只写原文有依据的，每条附一小句证据（不超过 25 字）；日常寒暄不算关系。"
)
VERDICT_RULES = (
    "判断一条身份关系是否被证据支持。只输出 JSON {verdict, why}。verdict：supports = 这句原文明确说明该关系成立；"
    "contradicts = 原文说明该关系不成立（如“她绝不是艾琳娜”）；insufficient = 证据不足以确定（猜测、反问、第三人的怀疑）。\n"
)
SAME_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["same", "why"],
               "properties": {"same": {"type": "string", "enum": ["same", "different", "unsure"]}, "why": {"type": "string"}}}
SAME_RULES = (
    "人物账本里有两条记录，判断它们是不是同一个人。只输出 JSON {same, why}。same = 同一个人（同一人的全名与简称、带头衔与不带头衔、"
    "正名与称呼）；different = 不同的人（包括双胞胎、同姓的亲属、长得像的人、同一称呼指向的另一个人）；unsure = 依据不足。"
    "只依据给出的原文和描述判断，不要凭名字相似猜。\n"
)


# ----------------------------------------------------------------------------------------------- storage
def store(novel_dir: Path) -> Path:
    path = Path(novel_dir) / "entity"
    for sub in ("mentions", "raw", "relations"):
        (path / sub).mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def chapter_texts(novel_dir: Path) -> dict[int, str]:
    """Chapter text as the planned chapters hold it (segments.json), for chapters that were planned."""
    out: dict[int, str] = {}
    novel_dir = Path(novel_dir)
    for path in novel_dir.glob(f"{novel_dir.name}_*/segments.json"):
        index = path.parent.name.rsplit("_", 1)[-1]
        if index.isdigit():
            rows = read_json(path, [])
            out[int(index)] = "\n".join(str(r.get("text") or "") for r in rows if isinstance(r, dict))
    return out


def novel_texts(novel_dir: Path) -> dict[int, str]:
    """Every chapter's source text, planned or not: from the novel file named in novel.json, else the segments."""
    novel_dir = Path(novel_dir)
    meta = read_json(novel_dir / "novel.json", {})
    try:
        from novel_manga.ingest import read_novel
        novel = read_novel(Path(meta["source"]).resolve(), novel_id=novel_dir.name)
        return {ep.index: ep.source_text for ep in novel.episodes}
    except Exception:  # noqa: BLE001 - no novel.json or source file: fall back to what was planned
        return chapter_texts(novel_dir)


def segment_texts(novel_dir: Path, chapter: int) -> dict[str, str]:
    novel_dir = Path(novel_dir)
    rows = read_json(novel_dir / f"{novel_dir.name}_{chapter}" / "segments.json", [])
    return {str(r.get("segment_id")): str(r.get("text") or "") for r in rows if isinstance(r, dict)}


def card_ids(novel_dir: Path) -> dict[str, str]:
    return {row["name"]: row["asset_id"] for row in read_json(Path(novel_dir) / "series_assets" / "manifest.json", {}).get("characters", [])
            if row.get("name") and row.get("asset_id")}


def seed_entities(novel_dir: Path) -> list[dict]:
    """The bible's cast keeps its ids and cards; everything the book adds comes after."""
    bible = read_json(Path(novel_dir) / "story_bible.json", {})
    cards = card_ids(novel_dir)
    entities = []
    for number, c in enumerate(bible.get("characters", []), start=1):
        name = str(c.get("name") or "").strip()
        if name:
            entities.append({"id": f"e{number:03d}", "canonical": name, "kind": "person", "asset_id": cards.get(name),
                             "role": str(c.get("role") or "")[:40], "description": str(c.get("appearance") or "")[:80],
                             "named": True, "source": "bible", "status": "active", "merged_into": None})
    return entities


# ----------------------------------------------------------------------------------------------- evidence
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
        if span is None or r.get("base") not in RELATION_BASES or r.get("stance") not in RELATION_STANCES:
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
        if span is None or c.get("type") not in CLAIM_TYPES:
            dropped.append({"chapter": chapter, "what": "claim", "type": c.get("type"), "why": "证据不在本章"})
            continue
        claims.append({"chapter": chapter, "type": c["type"], "subject": str(c.get("subject") or "").strip(), "object": str(c.get("object") or "").strip(),
                       "scope": c.get("scope") or "reality", "hidden_from_reader": bool(c.get("hidden_from_reader")),
                       "evidence": str(c.get("evidence"))[:160], "span": list(span)})
    return mentions, news, claims, dropped


# ----------------------------------------------------------------------------------------------- model calls
TITLES = ("公主", "王子", "先生", "小姐", "夫人", "女士", "太太", "医生", "教授", "博士", "男爵", "伯爵", "侯爵", "公爵", "国王", "女王", "陛下",
          "殿下", "老师", "队长", "船长", "神父", "修女", "警官", "警长", "探长", "侦探", "老板", "掌柜", "长老", "宗主", "道君", "真人", "仙子")


def retrieval_keys(name: str) -> set[str]:
    """Pieces of a name likely to stand alone in the text - the parts around ·, the name without its title, the
    first and last two characters of a Chinese name.  Retrieval only: they decide who is *offered*, never who is
    meant (the bible says 莱恩·格雷 and 薇奥拉公主; the chapters say 莱恩 and 薇奥拉)."""
    keys: set[str] = set()
    for part in re.split(r"[·・.\s]+", name):
        for title in TITLES:
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
    return f"{EXTRACT_RULES}\n\n人物账本：\n{book or '（空）'}\n\n本章原文：\n{chapter_text[:12000]}"


LEAN_NOTE = ("\n\n本章人物很多：mentions 只记最重要的 30 种写法，relations 最多 8 条，evidence 都不超过 15 字。")


def extract_chapter(chapter: int, chapter_text: str, candidates: list[dict], forms: dict[str, set[str]]) -> dict:
    """One reading; a chapter so crowded that the answer overflows the budget is read again with a lean brief."""
    prompt = extract_prompt(candidates, forms, chapter_text)
    try:
        answer = ask_json([{"type": "text", "text": prompt}], EXTRACT_SCHEMA, name="entity_extract", max_tokens=4500)
    except Exception as error:  # noqa: BLE001
        if "truncated" not in str(error):
            return {"chapter": chapter, "error": f"{type(error).__name__}: {error}"[:200]}
        try:
            answer = ask_json([{"type": "text", "text": prompt + LEAN_NOTE}], EXTRACT_SCHEMA, name="entity_extract", max_tokens=4500)
            answer["lean"] = True
        except Exception as again:  # noqa: BLE001
            return {"chapter": chapter, "error": f"{type(again).__name__}: {again}"[:200]}
    return {"chapter": chapter, "candidates": [e["id"] for e in candidates], **answer}


RELINK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["entity", "why"],
                 "properties": {"entity": {"type": "string"}, "why": {"type": "string"}}}


def judge_link(form: str, evidence: str, assigned: dict, options: list[dict], forms: dict[str, set[str]]) -> dict:
    """The model put a written form on a record whose name it shares nothing with (路易斯小姐 on 那位女士): asked
    again with the sentence and the records whose names the form does carry.  Answer: an id, NEW, or UNCERTAIN."""
    rows = "\n".join(f"[{e['id']}] {describe_record(e)}；已见写法：{'、'.join(sorted(forms.get(e['id'], set()))[:6])}" for e in [assigned, *options])
    text = (f"原文这句里的写法「{form}」指的是下面哪条记录？只输出 JSON {{entity, why}}，entity 填记录编号；都不是但确实是个新人物填 NEW；"
            f"无法确定填 UNCERTAIN。写法带名字时，优先名字对得上的记录；不是名字的泛称记录（吟游诗人、那位女士）只有原文明说是同一人才选。"
            f"\n原文：{evidence}\n候选记录：\n{rows}")
    try:
        return ask_json([{"type": "text", "text": text}], RELINK_SCHEMA, name="entity_relink", max_tokens=200)
    except Exception as error:  # noqa: BLE001
        return {"entity": "UNCERTAIN", "why": f"error {type(error).__name__}"}


def shares_a_piece(form: str, e: dict, forms: dict[str, set[str]]) -> bool:
    keys = {k for k in retrieval_keys(e["canonical"]) | set(forms.get(e["id"], ())) if len(k) >= 2 and k not in WEAK_KEYS}
    return any(k in form or form in k for k in keys)


def generic_name(name: str) -> bool:
    """A label rather than a name: 六秘环师, 那位女士, 怪物, 警员 - never the survivor of a merge, never merged into
    automatically (a rank absorbs a person, and then everyone of that rank becomes him)."""
    titled = next((t for t in sorted(WEAK_KEYS, key=len, reverse=True) if name.endswith(t) and len(name) > len(t)), "")
    short = "·" not in name and (len(name) <= 2 or (len(name) == 3 and re.search(r"[女男人师猫狗声魂神鬼仆兵王主客者员]", name)))
    return bool(short or name[:1] in "那这某" or (titled and len(name) - len(titled) <= 2) or re.search(r"(秘环师|教士|警官|管家|女仆|仆人|侍女|士兵|护卫|保镖|老人|老者|青年|少年|少女|男人|女人|怪物|巨人)$", name))


def judge_claim(claim: dict, subject: str, object_: str) -> dict:
    """Does the quotation support this relation between these two records?  Both sides are described (not just
    named) so that a right sentence attached to the wrong person - "占据了别人身体" resolved to the wrong body -
    reads as insufficient."""
    text = (VERDICT_RULES + f"关系类型：{claim['type']}（叙事范围：{claim['scope']}）\n主体（subject）：{subject}\n客体（object）：{object_}\n"
            f"证据原文：{claim['evidence']}\n这句证据是否明确支持“主体 {claim['type']} 客体”这条关系，而且客体确实是这条记录、不是别人？")
    try:
        return ask_json([{"type": "text", "text": text}], VERDICT_SCHEMA, name="entity_verdict", max_tokens=200)
    except Exception as error:  # noqa: BLE001
        return {"verdict": "insufficient", "why": f"error {type(error).__name__}"}


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


def judge_same(a: dict, b: dict, forms_a: set[str], forms_b: set[str], note: str = "") -> dict:
    """Are two records one person?  Asked when a new record's name shares a piece with an existing one (the
    read-ahead extracts chapters before the ledger has the people the previous chapter added), and when one
    written form bridges two records (艾琳娜·路易斯 for 艾琳娜 and 多洛茜·路易斯)."""
    text = (SAME_RULES + f"记录甲：{describe_record(a)}；已见写法：{'、'.join(sorted(forms_a))}\n"
            f"记录乙：{describe_record(b)}；已见写法：{'、'.join(sorted(forms_b))}" + (f"\n本章原文：{note}" if note else ""))
    try:
        return ask_json([{"type": "text", "text": text}], SAME_SCHEMA, name="entity_same", max_tokens=200)
    except Exception as error:  # noqa: BLE001
        return {"same": "unsure", "why": f"error {type(error).__name__}"}


WEAK_KEYS = set(TITLES) | {"女人", "男人", "女孩", "男孩", "老人", "青年", "少年", "少女", "孩子", "小孩", "姑娘", "大人", "那个", "这个", "那位", "这位"}


def lookalike_pairs(new: dict, entities: list[dict], forms: dict[str, set[str]], skip: set[str], limit: int = 3) -> list[dict]:
    """Records whose name shares a real piece with the new one (not a bare title or 女人) - earlier records only,
    so that two records created in the same chapter are compared once, from the later one's side."""
    keys = {k for k in retrieval_keys(new["canonical"]) | {new["canonical"]} if k not in WEAK_KEYS}
    scored = []
    for e in entities:
        if e["status"] != "active" or e["id"] in skip or _number(e["id"]) >= _number(new["id"]):
            continue
        theirs = {k for k in retrieval_keys(e["canonical"]) | set(forms.get(e["id"], ())) if k not in WEAK_KEYS}
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
        return {k for k in retrieval_keys(e["canonical"]) | set(forms.get(e["id"], ())) if len(k) >= 2 and k not in WEAK_KEYS and k in form}

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


# ----------------------------------------------------------------------------------------------- the ledger
def _number(eid: str) -> int:
    return int(eid[1:]) if eid[1:].isdigit() else 10 ** 9


class Ledger:
    """State under <novel>/entity/: entities (records, merged ones kept with merged_into), claims, merges, dropped,
    and one mentions file per chapter.  Extraction may run in a pool ahead of the ordered resolution; the
    snapshot it takes of the candidates is guarded by a lock, so is every mutation."""

    def __init__(self, novel_dir: Path):
        self.novel_dir = Path(novel_dir)
        self.base = store(self.novel_dir)
        self.lock = threading.Lock()
        self.entities: list[dict] = read_json(self.base / "entities.json", None) or seed_entities(self.novel_dir)
        self.claims: list[dict] = read_json(self.base / "claims.json", [])
        self.merges: list[dict] = read_json(self.base / "merges.json", [])
        self.dropped: list[dict] = read_json(self.base / "dropped.json", [])
        # a person's decisions live apart from what the model produced, and are applied again whenever a chapter
        # is re-read - the model's output is never edited in place (the same boundary graph-every-novel and the
        # screenplay analyzer keep: raw analysis kept, corrections separate, views recomputed)
        self.corrections: list[dict] = read_json(self.base / "corrections.json", [])
        self.asked: dict[str, str] = read_json(self.base / "asked.json", {})  # "eA|eB" -> same/different/unsure, asked once
        self.by_id = {e["id"]: e for e in self.entities}
        self.by_name: dict[str, dict] = {}
        self.forms: dict[str, set[str]] = {e["id"]: {e["canonical"]} for e in self.entities}
        self.recent: dict[str, int] = {}  # entity -> last chapter it was on stage or a voice
        for path in sorted((self.base / "mentions").glob("ch_*.json")):
            for m in read_json(path, []):
                if m.get("kind") == "proper" and m.get("entity") in self.forms:
                    self.forms[m["entity"]].add(m["form"])
                if m.get("presence") != "mentioned" and str(m.get("entity", "")).startswith("e"):
                    self.recent[m["entity"]] = max(self.recent.get(m["entity"], 0), int(m.get("chapter", 0)))
        for e in self.entities:  # a merged record's forms belong to its survivor
            if e.get("merged_into"):
                self.forms[self.canonical(e["id"])] |= self.forms.pop(e["id"], set())
        for e in self.entities:
            self.by_name.setdefault(e["canonical"], self.by_id[self.canonical(e["id"])])
        self.link_cards()

    # ---- lookups
    def canonical(self, eid: str) -> str:
        seen = set()
        while self.by_id.get(eid, {}).get("merged_into") and eid not in seen:
            seen.add(eid)
            eid = self.by_id[eid]["merged_into"]
        return eid

    def name_of(self, ref: str) -> str:
        return self.by_id.get(ref, {}).get("canonical", ref)

    def raw_path(self, chapter: int) -> Path:
        return self.base / "raw" / f"ch_{chapter:04d}.json"

    def mentions_path(self, chapter: int) -> Path:
        return self.base / "mentions" / f"ch_{chapter:04d}.json"

    def relations_path(self, chapter: int) -> Path:
        return self.base / "relations" / f"ch_{chapter:04d}.json"

    def has_chapter(self, chapter: int) -> bool:
        return self.mentions_path(chapter).is_file()

    def chapters_read(self) -> list[int]:
        return sorted(int(p.stem[3:]) for p in (self.base / "mentions").glob("ch_*.json") if p.stem[3:].isdigit())

    def link_cards(self) -> None:
        cards = card_ids(self.novel_dir)
        for e in self.entities:
            if not e.get("asset_id") and e["canonical"] in cards:
                e["asset_id"] = cards[e["canonical"]]

    def _add_entity(self, name: str, kind: str, named: bool, description: str, chapter: int, evidence: str = "", span=None) -> dict:
        entity = {"id": f"e{len(self.entities) + 1:03d}", "canonical": name, "kind": kind, "asset_id": None, "role": "",
                  "description": description, "named": named, "source": f"ch{chapter}", "status": "active", "merged_into": None}
        if evidence:
            entity["evidence"], entity["span"] = evidence, list(span or [])
        self.entities.append(entity)
        self.by_id[entity["id"]] = entity
        self.by_name[name] = entity
        self.forms[entity["id"]] = {name}
        return entity

    def _resolve_ref(self, ref: str, local_new: dict[str, str]) -> str | None:
        ref = (ref or "").strip()
        if ref in self.by_id:
            return self.canonical(ref)
        name = ref[4:].strip() if ref.startswith("NEW:") else ref
        if name in local_new:
            return local_new[name]
        if name in self.by_name:
            return self.canonical(self.by_name[name]["id"])
        return None

    # ---- stages
    def extract(self, chapter: int, text: str, force: bool = False) -> dict:
        """One model call, safe in a pool: the candidates are a snapshot of the ledger as it is now.  The reading
        remembers how far the ledger reached, so that resolve_chapter can tell when it was made too early."""
        raw = None if force else read_json(self.raw_path(chapter), None)
        if raw is not None and not raw.get("error"):
            return raw
        with self.lock:
            entities = [dict(e) for e in self.entities]
            forms = {k: set(v) for k, v in self.forms.items()}
            recent = {self.canonical(eid) for eid, seen in self.recent.items() if 0 <= chapter - seen <= RECENT_WINDOW}
        raw = extract_chapter(chapter, text, offered(entities, forms, text, recent), forms)
        raw["known_through"] = max((_number(e["id"]) for e in entities), default=0)
        if not raw.get("error"):
            write_json(self.raw_path(chapter), raw)
        return raw

    def stale(self, raw: dict, text: str) -> list[dict]:
        """Records that came into the ledger after this reading was made, are named in this chapter, and were not
        handled right by the early reading: a form carrying their name was put on some other record, or left
        UNCERTAIN.  (A NEW: with their name resolves to them by name and needs no second reading.)  The model never
        saw these records, so what it did with those names was a guess (多洛茜·路易斯小姐 put on 吟游诗人)."""
        known = raw.get("known_through")
        if known is None or raw.get("fresh"):
            return []
        late = []
        for e in self.entities:
            if _number(e["id"]) <= known or e["status"] != "active":
                continue
            keys = {k for k in retrieval_keys(e["canonical"]) | self.forms.get(e["id"], set()) if len(k) >= 2 and k not in WEAK_KEYS}
            if not any(k in text for k in keys):
                continue
            for m in raw.get("mentions") or []:
                form = str(m.get("form") or "")
                if not any(k in form for k in keys):
                    continue
                ref = str(m.get("entity") or "")
                if ref.startswith("NEW:") and self._resolve_ref(ref, {}) == self.canonical(e["id"]):
                    continue  # named it, and the name resolves to this very record
                if ref in self.by_id and shares_a_piece(form, self.by_id[self.canonical(ref)], self.forms):
                    continue  # put on a record whose name it carries: not a stale guess
                late.append(e)
                break
        return late

    def resolve_chapter(self, chapter: int, text: str, raw: dict | None = None, workers: int = 4) -> dict | None:
        """In chapter order: mentions get entity ids (new people get records), claims get verdicts and a status,
        supported same_as claims become merge edges.  Re-running a chapter replaces its mentions and claims."""
        raw = raw if raw is not None else read_json(self.raw_path(chapter), None)
        if raw is None or raw.get("error"):
            return None
        late = self.stale(raw, text)
        if late:  # read again, now that the people this chapter names are on the books
            again = self.extract(chapter, text, force=True)
            if not again.get("error"):
                again["fresh"] = True
                write_json(self.raw_path(chapter), again)
                raw = again
        mentions, news, claims, dropped = ground(chapter, text, raw)
        with self.lock:
            self.dropped.extend(dropped)
            local_new: dict[str, str] = {}
            created: list[dict] = []
            relinks: list[tuple[dict, dict, list[dict]]] = []  # (mention, assigned record, records the form's pieces name)
            for n in news:
                if n["name"] in self.by_name:
                    local_new[n["name"]] = self.canonical(self.by_name[n["name"]]["id"])
                    continue
                created.append(self._add_entity(n["name"], n["kind"], n["named"], n["description"], chapter, n["evidence"], n["span"]))
                local_new[n["name"]] = created[-1]["id"]
            for m in mentions:
                eid = self._resolve_ref(m["entity"], local_new)
                if eid is None and m["entity"].startswith("NEW:"):
                    name = m["entity"][4:].strip() or m["form"]
                    created.append(self._add_entity(name, "person", m["kind"] == "proper", "", chapter, m["evidence"], m["span"]))
                    eid = created[-1]["id"]
                    local_new[name] = eid
                claimed = re.sub(r"[（(].*$", "", m.pop("entity_name", "")).strip()  # the prompt shows 名字（身份）
                if eid and claimed and self.name_of(eid) != claimed and claimed not in self.forms.get(eid, ()):
                    # the id and the name the model copied disagree: the name is the safer half of a slipped row
                    other = self.by_name.get(claimed)
                    self.dropped.append({"chapter": chapter, "what": "mention", "form": m["form"], "why": f"编号 {eid} 与名字 {claimed} 不符",
                                         "fixed": bool(other)})
                    eid = self.canonical(other["id"]) if other else None
                m["entity"] = eid or "UNCERTAIN"
                if eid and m["kind"] == "proper" and eid not in local_new.values() and not shares_a_piece(m["form"], self.by_id[eid], self.forms):
                    # a proper form on a record it shares no name piece with: a nickname (齐娜 for 卡珊德拉婆婆) or a slip
                    # (路易斯小姐 on 那位女士) - the model is asked once per form and record, with the sentence
                    cached = self.asked.get(f"{m['form']}|{eid}")
                    if cached is None:
                        options = [e for e in self.entities if e["status"] == "active" and e["id"] != eid and shares_a_piece(m["form"], e, self.forms)][:4]
                        relinks.append((m, self.by_id[eid], options))
                    elif cached.startswith("e") and cached in self.by_id:
                        m["entity"] = eid = self.canonical(cached)
                    elif cached == "UNCERTAIN":
                        m["entity"], eid = "UNCERTAIN", None
                if eid and m["kind"] == "proper":
                    self.forms.setdefault(eid, set()).add(m["form"])
                if eid and m["presence"] != "mentioned":
                    self.recent[eid] = max(self.recent.get(eid, 0), chapter)
        if relinks:  # model calls outside the lock, then the mentions are corrected before anything is written
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                answers = list(pool.map(lambda r: judge_link(r[0]["form"], r[0]["evidence"], r[1], r[2], self.forms), relinks))
            with self.lock:
                for (m, assigned, options), answer in zip(relinks, answers):
                    choice = str(answer.get("entity") or "UNCERTAIN").strip()
                    self.asked[f"{m['form']}|{assigned['id']}"] = choice
                    if choice == assigned["id"]:
                        continue
                    self.forms.get(assigned["id"], set()).discard(m["form"])
                    self.dropped.append({"chapter": chapter, "what": "mention", "form": m["form"], "why": f"改判 {assigned['canonical']} → {choice}: {str(answer.get('why') or '')[:60]}"})
                    if choice.startswith("e") and choice in self.by_id:
                        m["entity"] = self.canonical(choice)
                        self.forms.setdefault(m["entity"], set()).add(m["form"])
                    elif choice == "NEW":
                        created.append(self._add_entity(m["form"], "person", True, "", chapter, m["evidence"], m["span"]))
                        m["entity"] = created[-1]["id"]
                    else:
                        m["entity"] = "UNCERTAIN"
        with self.lock:
            write_json(self.mentions_path(chapter), mentions)
            relations = []
            for r in ground_relations(chapter, text, raw, self.dropped):
                a, b = self._resolve_ref(r["from"], local_new), self._resolve_ref(r["to"], local_new)
                if not a or not b or a == b:
                    self.dropped.append({"chapter": chapter, "what": "relation", "why": f"两端归不到记录 {r['from']}→{r['to']}"})
                    continue
                relations.append({**r, "from": a, "to": b})
            write_json(self.relations_path(chapter), relations)
            self.claims = [c for c in self.claims if c["chapter"] != chapter]
            pending = []
            for c in claims:
                for side_key in ("subject", "object"):  # a NEW:name the model used only inside a claim still gets a record
                    ref = c[side_key]
                    if ref.startswith("NEW:") and self._resolve_ref(ref, local_new) is None and ref[4:].strip():
                        created.append(self._add_entity(ref[4:].strip(), "person", True, "", chapter, c["evidence"], c["span"]))
                        local_new[ref[4:].strip()] = created[-1]["id"]
                s, o = self._resolve_ref(c["subject"], local_new), self._resolve_ref(c["object"], local_new)
                if c["type"] not in ONE_SIDED and s and o and s == o:
                    self.dropped.append({"chapter": chapter, "what": "claim", "type": c["type"], "why": f"自指 {self.name_of(s)}"})
                    continue
                pending.append({**c, "id": f"c{chapter:04d}-{len(pending) + 1:02d}", "subject": s or c["subject"], "object": o or c["object"],
                                "resolved": bool(s and (o or c["type"] in ONE_SIDED))})
            # a new record that shares a name piece with an older one (or with another new one): the read-ahead
            # may have extracted this chapter before the older record existed, so the model is asked with both in view
            questions = [(new, old, "") for new in created for old in lookalike_pairs(new, self.entities, self.forms, set())]
            # a written form that bridges two records (艾琳娜·路易斯): asked once per pair, the answer remembered
            for m in mentions:
                if m["kind"] != "proper" or not m["entity"].startswith("e"):
                    continue
                for other in bridge_pairs(m["form"], m["entity"], self.entities, self.forms):
                    key = "|".join(sorted((m["entity"], other["id"]), key=_number))
                    if key in self.asked or any({q[0]["id"], q[1]["id"]} == {m["entity"], other["id"]} for q in questions):
                        continue
                    questions.append((self.by_id[m["entity"]], other, m["evidence"]))
        if pending or questions:  # the model calls run outside the lock
            def side(ref: str) -> str:
                return describe_record(self.by_id[ref]) if ref in self.by_id else ref

            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                verdicts = list(pool.map(lambda r: judge_claim(r, side(r["subject"]), side(r["object"])), pending))
                answers = list(pool.map(lambda q: judge_same(q[0], q[1], self.forms.get(q[0]["id"], set()), self.forms.get(q[1]["id"], set()), q[2]),
                                        questions))
            with self.lock:
                for row, verdict in zip(pending, verdicts):
                    row["verdict"], row["why"] = verdict.get("verdict", "insufficient"), str(verdict.get("why") or "")[:120]
                    supported = row["verdict"] == "supports" and row["resolved"] and row["scope"] == "reality"
                    if supported and row["type"] not in ONE_SIDED and not all(self._evidence_names(x, row["evidence"]) for x in (row["subject"], row["object"])):
                        # "占据了别人身体" says nothing about whose body: a sentence that does not name both
                        # records cannot settle a relation between them, whatever the judge made of it
                        supported, row["why"] = False, "证据句没有点到两端的名字；" + row["why"][:100]
                    row["status"] = "pending" if row["hidden_from_reader"] or not supported else "accepted"
                    self._settle(row, chapter)
                for (new, old, note), answer in zip(questions, answers):
                    same = answer.get("same", "unsure")
                    self.asked["|".join(sorted((new["id"], old["id"]), key=_number))] = same
                    if same == "different" or self.canonical(new["id"]) == self.canonical(old["id"]):
                        continue
                    row = {"chapter": chapter, "type": "same_as", "subject": new["id"], "object": old["id"], "scope": "reality",
                           "hidden_from_reader": False, "evidence": note or new.get("evidence", ""), "span": new.get("span", []),
                           "source": "bridge" if note else "reconcile", "id": f"c{chapter:04d}-{len(pending) + 1:02d}", "resolved": True,
                           "verdict": "supports" if same == "same" else "insufficient", "why": str(answer.get("why") or "")[:120],
                           "status": "accepted" if same == "same" else "pending"}
                    pending.append(row)
                    self._settle(row, chapter)
        self.save()
        merged = [f"{self.name_of(m['from'])}→{self.name_of(m['into'])}" for m in self.merges if m["chapter"] == chapter]
        return {"chapter": chapter, "mentions": len(mentions), "new": [e["canonical"] for e in created if e["status"] == "active"],
                "claims": Counter(r["status"] for r in pending), "merged": merged, "relations": len(relations), "dropped": len(dropped),
                "reread": [e["canonical"] for e in late]}

    def relations_through(self, chapter: int) -> list[dict]:
        """Every relation row read so far up to and including this chapter, endpoints canonical."""
        rows = []
        for c in self.chapters_read():
            if c > chapter:
                break
            for r in read_json(self.relations_path(c), []):
                rows.append({**r, "from": self.canonical(r["from"]), "to": self.canonical(r["to"])})
        return rows

    def _evidence_names(self, ref: str, evidence: str) -> bool:
        """Does the quotation carry a piece of this record's name (or a form it has been called by)?"""
        if ref not in self.by_id:
            return True  # free text, e.g. what someone transformed into
        e = self.by_id[ref]
        keys = {k for k in retrieval_keys(e["canonical"]) | self.forms.get(e["id"], set()) if len(k) >= 2 and k not in WEAK_KEYS}
        return any(k in evidence for k in keys)

    def _correction_for(self, row: dict) -> dict | None:
        """The person's standing decision on this relation, if any: matched by type and the two records (either
        order for the symmetric types), not by claim id, so it survives a chapter being read again."""
        pair = {self.canonical(row["subject"]), self.canonical(row["object"])}
        for c in self.corrections:
            if c["type"] == row["type"] and {self.canonical(c["subject"]), self.canonical(c["object"])} == pair:
                return c
        return None

    def _settle(self, row: dict, chapter: int) -> None:
        """Apply a standing correction, then act on the status: an accepted same_as merges; the row is recorded.
        A merge that would fold a person into a label (杰克·德昂 into 六秘环师) waits for a person instead."""
        correction = self._correction_for(row)
        if correction:
            row["status"], row["corrected"] = correction["decision"], correction.get("note", "")
        elif (row["status"] == "accepted" and row["type"] in MERGING
              and any(generic_name(self.name_of(x)) and self.by_id[x].get("source") == "bible" for x in (row["subject"], row["object"]) if x in self.by_id)):
            # a label the bible carries as a character (六秘环师, 那位女士) may name several people over the book;
            # a label the ledger itself introduced in one chapter (作家小姐) is that chapter's person and may merge
            row["status"], row["why"] = "pending", "一方是圣经里的泛称记录，合并需人工确认；" + str(row.get("why") or "")[:100]
        if row["status"] == "accepted" and row["type"] in MERGING and row["subject"] != row["object"]:
            self._merge(row["subject"], row["object"], row["id"], chapter)
        self.claims.append(row)

    def decide(self, claim_id: str, decision: str, note: str = "") -> bool:
        """A person accepts or rejects a claim.  The decision is stored apart (corrections.json) and applied
        now: accepting a same_as merges, rejecting one whose merge went through undoes that merge."""
        row = next((c for c in self.claims if c["id"] == claim_id), None)
        if row is None or decision not in ("accepted", "rejected"):
            return False
        with self.lock:
            pair = {self.canonical(row["subject"]), self.canonical(row["object"])}
            self.corrections = [c for c in self.corrections if c.get("claim") != claim_id
                                and not (c["type"] == row["type"] and {self.canonical(c["subject"]), self.canonical(c["object"])} == pair)]
            self.corrections.append({"claim": claim_id, "type": row["type"], "subject": row["subject"], "object": row["object"],
                                     "decision": decision, "note": note, "by": "human", "at": time.strftime("%Y-%m-%d %H:%M")})
            row["status"], row["corrected"] = decision, note
            merged = any(m["claim"] == claim_id for m in self.merges)
            if decision == "accepted" and row["type"] in MERGING and not merged and row["subject"] != row["object"]:
                self._merge(row["subject"], row["object"], claim_id, row["chapter"])
        if decision == "rejected" and merged:
            self.undo_merge(claim_id)
        self.save()
        return True

    def _merge(self, a: str, b: str, claim_id: str, chapter: int) -> None:
        keep, drop = sorted((a, b), key=_number)  # the earlier record survives; the bible's ids come first...
        if generic_name(self.by_id[keep]["canonical"]) and not generic_name(self.by_id[drop]["canonical"]):
            keep, drop = drop, keep  # ...unless the earlier one is a label and the later one a name
        if self.by_id[drop]["status"] != "active" or self.canonical(keep) == self.canonical(drop):
            return
        self.by_id[drop]["status"], self.by_id[drop]["merged_into"] = "merged", keep
        self.forms.setdefault(keep, set()).update(self.forms.pop(drop, set()))
        self.by_name[self.by_id[drop]["canonical"]] = self.by_id[keep]
        self.merges.append({"claim": claim_id, "from": drop, "into": keep, "chapter": chapter})

    def undo_merge(self, claim_id: str) -> bool:
        """Reverse one merge: the record comes back, its forms go with it, the claim is marked rejected."""
        with self.lock:
            row = next((m for m in self.merges if m["claim"] == claim_id), None)
            if row is None:
                return False
            drop = self.by_id[row["from"]]
            drop["status"], drop["merged_into"] = "active", None
            own = {m["form"] for c in self.chapters_read() for m in read_json(self.mentions_path(c), [])
                   if m.get("kind") == "proper" and m.get("entity") == drop["id"]} | {drop["canonical"]}
            self.forms[drop["id"]] = own
            self.forms[row["into"]] -= own - {self.by_id[row["into"]]["canonical"]}
            self.by_name[drop["canonical"]] = drop
            self.merges = [m for m in self.merges if m["claim"] != claim_id]
            for c in self.claims:
                if c["id"] == claim_id:
                    c["status"] = "rejected"
        self.save()
        return True

    def scan_rows(self, chapter: int) -> list[dict]:
        """The chapter's people in the shape grow_bible reads (name, kind, mentions, speaks_or_close_up)."""
        rows: dict[str, dict] = {}
        for m in read_json(self.mentions_path(chapter), []):
            if not str(m.get("entity", "")).startswith("e"):
                continue
            eid = self.canonical(m["entity"])
            e = self.by_id.get(eid)
            if e is None:
                continue
            row = rows.setdefault(eid, {"name": e["canonical"], "kind": "具名角色" if e.get("named", True) else "称呼或身份",
                                        "mentions": 0, "speaks_or_close_up": False})
            row["mentions"] += int(m.get("count", 1))
            row["speaks_or_close_up"] = row["speaks_or_close_up"] or m.get("presence") in ("on_stage", "voice")
        return list(rows.values())

    def save(self) -> None:
        with self.lock:
            write_json(self.base / "entities.json", self.entities)
            write_json(self.base / "claims.json", self.claims)
            write_json(self.base / "merges.json", self.merges)
            write_json(self.base / "dropped.json", self.dropped)
            write_json(self.base / "corrections.json", self.corrections)
            write_json(self.base / "asked.json", self.asked)


# ----------------------------------------------------------------------------------------------- pass drivers
DEDUP_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["same_ids", "why"],
                "properties": {"same_ids": {"type": "array", "items": {"type": "string"}}, "why": {"type": "string"}}}


def generic_records(ledger: "Ledger") -> list[dict]:
    """Bible records that are not proper names - 那位女士, 秘女, 灵魂, 调查师, 白猫: the book's cast list was built by
    name scans and holds the same being under several such labels."""
    from thin_review import APPELLATION, GENERIC_NAMES
    out = []
    for e in ledger.entities:
        if e["status"] != "active" or e.get("source") != "bible":
            continue
        name = e["canonical"]
        if name in GENERIC_NAMES or APPELLATION.search(name) or generic_name(name) or not e.get("named", True):
            out.append(e)
    return out


def dedup_generic(ledger: "Ledger", workers: int = 4) -> list[dict]:
    """One question per generic bible record: which of the other generic records is the same being?  The bible's
    descriptions are the card builder's inventions, not the book's words, so nothing merges on them: every pair
    the model names is a pending claim for a person (both sides naming each other is noted in the verdict)."""
    rows = generic_records(ledger)
    if len(rows) < 2:
        return []

    def ask(e: dict) -> dict:
        others = "\n".join(f"[{o['id']}] {describe_record(o)}" for o in rows if o["id"] != e["id"])
        text = (SAME_RULES + f"目标记录：[{e['id']}] {describe_record(e)}\n候选记录：\n{others}\n"
                "只输出 JSON {same_ids, why}：same_ids 是与目标记录确为同一个存在的候选编号列表（没有就空数组）。只凭描述能确定的才算。")
        try:
            return ask_json([{"type": "text", "text": text}], DEDUP_SCHEMA, name="entity_dedup", max_tokens=500)
        except Exception as error:  # noqa: BLE001
            return {"same_ids": [], "why": f"error {type(error).__name__}"}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        answers = list(pool.map(ask, rows))
    votes: dict[str, list[str]] = {}
    for e, answer in zip(rows, answers):
        for other in answer.get("same_ids") or []:
            if other in ledger.by_id and other != e["id"]:
                votes.setdefault("|".join(sorted((e["id"], other), key=_number)), []).append(str(answer.get("why") or "")[:120])
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
            ledger._settle(row, 0)
            made.append(row)
    ledger.save()
    return made


def run_extract(novel_dir: Path, chapters: list[int], workers: int, texts: dict[int, str] | None = None) -> None:
    ledger = Ledger(novel_dir)
    texts = texts or novel_texts(novel_dir)
    todo = [c for c in chapters if c in texts and not (ledger.raw_path(c).is_file() and not read_json(ledger.raw_path(c), {}).get("error"))]
    print(f"{Path(novel_dir).name}: 抽取 {len(todo)} 章（已有 {len(chapters) - len(todo)}），{workers} 路", flush=True)
    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for done, (c, raw) in enumerate(zip(todo, pool.map(lambda c: ledger.extract(c, texts[c]), todo)), start=1):
            if raw.get("error"):
                print(f"  ch{c}: 出错 {raw['error']}", flush=True)
            if done % 25 == 0 or done == len(todo):
                print(f"  …{done}/{len(todo)} 章，{done / max(time.time() - started, 1) * 60:.1f} 章/分", flush=True)


def run_resolve(novel_dir: Path, chapters: list[int], workers: int, texts: dict[int, str] | None = None) -> None:
    ledger = Ledger(novel_dir)
    texts = texts or novel_texts(novel_dir)
    for c in chapters:
        if c not in texts:
            continue
        summary = ledger.resolve_chapter(c, texts[c], workers=workers)
        if summary:
            print(f"  ch{c}: 指称 {summary['mentions']}，新记录 {summary['new'][:5]}，判断 {dict(summary['claims'])}，丢弃 {summary['dropped']}", flush=True)


# ----------------------------------------------------------------------------------------------- settling
SETTLE_RULES = (
    "下面是一本小说的人物账本里两条记录，以及一条关于它们的关系判断。它在单章的证据不够，现在把全书里能找到的证据都给你：两条记录各自的描述和写法、"
    "各自在场的章、两人同时在场的章（同一场里同时出现的两个人不可能是同一个人，除非原文写了分身或幻象）、所有相关证据句、后文原句、人物关系记录。"
    "只依据这些证据判断这条关系是否成立，只输出 JSON {verdict, why}：supports = 成立；contradicts = 不成立；insufficient = 全书证据仍不够。"
)
SETTLE_RULES_B = (
    "你是审稿人，要推翻下面这条人物关系判断。先找能证明它不成立的证据（两人同场、称呼矛盾、后文明说是两个人），找不到再看支持它的证据。"
    "只输出 JSON {verdict, why}：contradicts = 有证据说明不成立；supports = 找不到反证而且有明确证据支持；insufficient = 两边都不够。"
)


SETTLE_RULES_TIEBREAK = (
    "两位审读对下面这条人物关系判断意见不一。你是终审，必须给出结论，不能说证据不够：把全书证据里最硬的几条摆出来（同场出现、称呼、后文明说），"
    "按证据多的一边定。只输出 JSON {verdict, why}：supports = 成立；contradicts = 不成立。拿不准时按不成立（两条记录分开处理）。"
)
SETTLE_RULES_BODY = (
    "这是一条换身体、化身或冒充类关系，它决定画面里该画成谁的样子。只回答一个问题：全书证据里有没有原文明说“谁在谁的身体里”或“谁扮成了谁”，"
    "并且那具身体（或被冒充的人）就是记录乙？只输出 JSON {verdict, why}：supports = 原文明说了且就是记录乙；contradicts = 没明说，或不是记录乙。"
)


def evidence_pack(ledger: "Ledger", a: str, b: str, claim_type: str, limit: int = 6) -> str:
    """Everything the book has said about two records, for one settling question."""
    chapters = ledger.chapters_read()
    on_stage: dict[str, set[int]] = {a: set(), b: set()}
    quotes: dict[str, list[str]] = {a: [], b: []}
    for c in chapters:
        for m in read_json(ledger.mentions_path(c), []):
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
            relations.append(f"第{r['chapter']}章 {ledger.name_of(r['from'])}→{ledger.name_of(r['to'])}：{RELATION_BASES.get(r['base'], r['base'])}/{RELATION_STANCES.get(r['stance'], r['stance'])}"
                             + (f"，称呼「{r['address']}」" if r.get("address") else ""))

    def side(eid: str) -> str:
        e = ledger.by_id[eid]
        chs = sorted(on_stage[eid])
        return (f"{describe_record(e)}；已见写法：{'、'.join(sorted(ledger.forms.get(eid, {e['canonical']}))[:10])}；"
                f"在场 {len(chs)} 章" + (f"（{chs[0]}–{chs[-1]}）" if chs else "") + "\n  原句：" + (" / ".join(quotes[eid]) or "无"))

    labels = [ledger.name_of(x) for x in (a, b) if generic_name(ledger.name_of(x)) and ledger.by_id[x].get("source") == "bible"]
    return (f"关系：{ledger.name_of(a)} {claim_type} {ledger.name_of(b)}\n记录甲：{side(a)}\n记录乙：{side(b)}\n"
            f"两人同时在场的章：{together[:12] if together else '没有'}\n"
            + "相关证据句：\n" + "\n".join(f"  第{c['chapter']}章（{c.get('source', 'text')}，{c.get('verdict')}）：{c['evidence']}" for c in related[:8])
            + ("\n人物关系记录：\n  " + "\n  ".join(relations) if relations else "")
            + (f"\n注意：「{'」「'.join(labels)}」是称呼或身份而不是名字。这种记录只有在全书里始终只指这一个人时（如“那位女士”后来揭晓就是某人）才能算同一人；"
               "若它在书里指过不止一个人（女仆、秘女、护卫这类），就判不成立。" if labels else "")
            + ("\n注意：这是换身体/化身类关系，两人同时被提到不构成反证——灵魂和身体的主人本来就会一起出现；要看的是原文有没有明说谁在谁的身体里。"
               if claim_type in BODY_TYPES else ""))


def judge_settle(pack: str, rules: str) -> dict:
    try:
        return ask_json([{"type": "text", "text": rules + "（why 不超过 80 字）\n\n" + pack}], VERDICT_SCHEMA, name="entity_settle", max_tokens=600)
    except Exception as error:  # noqa: BLE001
        return {"verdict": "insufficient", "why": f"error {type(error).__name__}"}


def settle_pending(ledger: "Ledger", workers: int = 4) -> list[dict]:
    """Every pending claim between two records is judged once more with the whole book's evidence, by two
    judges asked in opposite directions.  Both supports -> accepted (a same_as merges, reversibly); both
    contradicts -> rejected; anything else stays pending with the two answers noted.  A person's standing
    decision (corrections.json) always wins and is never re-asked."""
    todo = [c for c in ledger.claims if c.get("status") == "pending" and c["subject"] in ledger.by_id and c["object"] in ledger.by_id
            and not ledger._correction_for(c) and ledger.canonical(c["subject"]) != ledger.canonical(c["object"])]
    seen: set[str] = set()
    pairs = []
    for c in todo:
        key = f"{c['type']}|" + "|".join(sorted((ledger.canonical(c["subject"]), ledger.canonical(c["object"])), key=_number))
        if key in seen:
            continue
        seen.add(key)
        pairs.append(c)
    packs = [evidence_pack(ledger, ledger.canonical(c["subject"]), ledger.canonical(c["object"]), c["type"]) for c in pairs]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        first = list(pool.map(lambda p: judge_settle(p, SETTLE_RULES), packs))
        second = list(pool.map(lambda p: judge_settle(p, SETTLE_RULES_B), packs))
        # No human queue (2026-09-14): a split vote goes to a third judge who must decide, and a body claim whose
        # quotations never name the body's owner gets one targeted question instead of waiting for a person.
        votes = [(x.get("verdict", "insufficient"), y.get("verdict", "insufficient")) for x, y in zip(first, second)]
        split = [i for i, v in enumerate(votes) if v not in (("supports", "supports"), ("contradicts", "contradicts"))]
        third = dict(zip(split, pool.map(lambda i: judge_settle(packs[i], SETTLE_RULES_TIEBREAK), split)))

        def owner_named(i: int) -> bool:
            c = pairs[i]
            a, b = ledger.canonical(c["subject"]), ledger.canonical(c["object"])
            twins = [r for r in ledger.claims if r["type"] == c["type"] and {ledger.canonical(r["subject"]), ledger.canonical(r["object"])} == {a, b}]
            return any(ledger._evidence_names(b, r["evidence"]) for r in twins)

        body_check = [i for i in range(len(pairs)) if pairs[i]["type"] in BODY_TYPES
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
            twins = [r for r in ledger.claims if r["type"] == c["type"] and {ledger.canonical(r["subject"]), ledger.canonical(r["object"])} == {a, b}]
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
            carded = bool(ledger.by_id[a].get("asset_id")) and bool(ledger.by_id[b].get("asset_id"))
            lead = any("主角" in str(ledger.by_id[x].get("role", "")) for x in (a, b))
            for r in twins:
                r["settled"], r["status"] = note, status
                # what a person should look at: a body claim, a merge of two carded records (the picture changes
                # either way), or anything touching a lead; a label folding into an uncarded record changes no picture
                r["priority"] = c["type"] in BODY_TYPES or (c["type"] in MERGING and carded) or lead
            if status in ("accepted", "rejected"):
                # remembered apart from the model's output, so that a chapter read again does not undo it; a person's
                # later decision on the same pair replaces it
                ledger.corrections = [k for k in ledger.corrections if not (k["type"] == c["type"] and {ledger.canonical(k["subject"]), ledger.canonical(k["object"])} == {a, b})]
                ledger.corrections.append({"claim": c["id"], "type": c["type"], "subject": a, "object": b, "decision": status, "note": note[:160],
                                           "by": "settle", "at": time.strftime("%Y-%m-%d %H:%M")})
            if status == "accepted" and c["type"] in MERGING:
                ledger._merge(c["subject"], c["object"], c["id"], c["chapter"])
            settled.append({"id": c["id"], "type": c["type"], "subject": ledger.name_of(c["subject"]), "object": ledger.name_of(c["object"]),
                            "status": status, "votes": vote, "why": note})
    settled.extend(close_leftovers(ledger))
    ledger.save()
    return settled


def close_leftovers(ledger: "Ledger") -> list[dict]:
    """Pending claims settling cannot ask about, closed without a person: a pair already merged (the judgment holds by
    itself) and one-sided event claims (death / transformation / reveal with no second record), which change no picture.
    Nothing stays pending after a settle - the ledger has no human queue (2026-09-14)."""
    closed = []
    with ledger.lock:
        for c in ledger.claims:
            if c.get("status") != "pending":
                continue
            s, o = c.get("subject"), c.get("object")
            if s in ledger.by_id and o in ledger.by_id and ledger.canonical(s) == ledger.canonical(o):
                c["status"], c["settled"] = "accepted", "两条记录已经合并，判断自然成立；自动结案"
            elif s not in ledger.by_id or o not in ledger.by_id:
                c["status"], c["settled"] = "closed", "单边事件判断（缺一方记录），不影响画面；自动结案"
            else:
                continue
            closed.append({"id": c.get("id"), "type": c["type"], "subject": ledger.name_of(s) if s in ledger.by_id else s,
                           "object": ledger.name_of(o) if o in ledger.by_id else o, "status": c["status"], "votes": None, "why": c["settled"]})
    return closed


# ----------------------------------------------------------------------------------------------- views
def build_index(novel_dir: Path) -> dict:
    """entity_index.json as the planner, packer and reviewer read it: proper forms per surviving record,
    contextual forms apart, mentions counted from the chapters, tier from the count."""
    ledger = Ledger(novel_dir)
    forms: dict[str, Counter] = {e["id"]: Counter() for e in ledger.entities}
    contextual: dict[str, set[str]] = {e["id"]: set() for e in ledger.entities}
    span: dict[str, list] = {e["id"]: [None, None] for e in ledger.entities}
    chapters = ledger.chapters_read()
    for c in chapters:
        for m in read_json(ledger.mentions_path(c), []):
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
    ledger = ledger or Ledger(novel_dir)
    disclosed_through = chapter if disclosed_through is None else disclosed_through
    text = novel_texts(novel_dir).get(chapter, "") if text is None else text
    window = None
    if segment_ids:
        segs = segment_texts(novel_dir, chapter)
        pieces = [segs[s] for s in segment_ids if s in segs]
        if pieces:  # the segments were cut from a cleaned copy: find them the way quotations are found
            head, tail = locate(pieces[0][:40], text), locate(pieces[-1][-40:], text)
            if head and tail and tail[1] > head[0]:
                window = (head[0], tail[1])
    accepted = [c for c in ledger.claims if c.get("status") == "accepted" and c["chapter"] <= chapter]
    body_of = {ledger.canonical(c["subject"]): ledger.canonical(c["object"]) for c in accepted
               if c["type"] == "occupies_body" and c["subject"] in ledger.by_id and c["object"] in ledger.by_id}
    hidden = [c for c in ledger.claims if c.get("hidden_from_reader") and c["chapter"] > disclosed_through]
    cast: dict[str, dict] = {}
    passage = text[window[0]:window[1]] if window else None
    for m in read_json(ledger.mentions_path(chapter), []):
        if passage is not None and m["form"] not in passage:
            continue  # a mention row is one form for the whole chapter: in the passage means the form occurs in it
        if not str(m["entity"]).startswith("e"):
            continue
        eid = ledger.canonical(m["entity"])
        body = body_of.get(eid, eid)
        row = cast.setdefault(eid, {"entity": eid, "name": ledger.name_of(eid), "presence": "mentioned", "names_spoken": set(),
                                    "body": body, "card": ledger.by_id.get(body, {}).get("asset_id")})
        if PRESENCE_RANK[m["presence"]] > PRESENCE_RANK[row["presence"]]:
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
    return {"policy": LEDGER_POLICY, "chapter": chapter, "segments": segment_ids,
            "cast": sorted(cast.values(), key=lambda r: -PRESENCE_RANK[r["presence"]]), "relations": relations, "must_not_reveal": secrets}


def pending_pages(novel_dir: Path, texts: dict[int, str] | None = None) -> list[Path]:
    """One page per chapter with pending claims: the chapter with the quotations and the two records' mentions
    highlighted.  Uses LangExtract's visualiser when it is installed (same span model), plain marks otherwise."""
    ledger = Ledger(novel_dir)
    texts = texts or novel_texts(novel_dir)
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
                  for m in read_json(ledger.mentions_path(chapter), []) if str(m["entity"]).startswith("e") and ledger.canonical(m["entity"]) in parties]
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


def parse_chapters(spec: str) -> list[int]:
    out: set[int] = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if part:
            a, _, b = part.partition("-")
            out.update(range(int(a), int(b or a) + 1))
    return sorted(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("extract", "resolve", "snapshot", "index", "pending", "undo", "accept", "reject", "dedup", "settle"))
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--chapters")
    parser.add_argument("--chapter", type=int)
    parser.add_argument("--segments")
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--out", type=Path, help="index: where to write (default <novel>/entity_index.json)")
    parser.add_argument("--html", action="store_true", help="pending: also write one highlighted page per chapter")
    parser.add_argument("--claim", help="undo/accept/reject: the claim id")
    parser.add_argument("--note", default="", help="accept/reject: why")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    if args.command == "extract":
        texts = novel_texts(novel_dir)
        run_extract(novel_dir, parse_chapters(args.chapters) if args.chapters else sorted(texts), args.workers, texts)
    elif args.command == "resolve":
        ledger = Ledger(novel_dir)
        chapters = parse_chapters(args.chapters) if args.chapters else sorted(int(p.stem[3:]) for p in (ledger.base / "raw").glob("ch_*.json"))
        run_resolve(novel_dir, chapters, args.workers)
    elif args.command == "snapshot":
        print(json.dumps(snapshot(novel_dir, args.chapter, args.segments.split(",") if args.segments else None), ensure_ascii=False, indent=1))
    elif args.command == "index":
        index = build_index(novel_dir)
        out = args.out or (novel_dir / "entity_index.json")
        write_json(out, index)
        print(f"{novel_dir.name}: {len(index['characters'])} 条记录，{index['chapters']} 章 → {out}")
    elif args.command == "pending":
        ledger = Ledger(novel_dir)
        rows = [c for c in ledger.claims if c.get("status") == "pending"]
        rows.sort(key=lambda c: (not c.get("priority"), c["chapter"]))  # what changes the picture first: bodies, card holders
        for c in rows:
            print(f"{'★' if c.get('priority') else ' '} ch{c['chapter']} {c['id']} {c['type']}: {ledger.name_of(c['subject'])} → {ledger.name_of(c['object'])} "
                  f"[{c.get('verdict')}] 隐瞒读者={c['hidden_from_reader']} | {(c.get('settled') or c['evidence'])[:70]}")
        print(f"待确认 {len(rows)} 条，其中 ★ 影响画面（换身体或有卡的人物）{sum(1 for c in rows if c.get('priority'))} 条")
        if args.html:
            for page in pending_pages(novel_dir):
                print(f"  {page}")
    elif args.command == "undo":
        print("撤销成功" if Ledger(novel_dir).undo_merge(args.claim or "") else "没有这条合并")
    elif args.command == "dedup":
        ledger = Ledger(novel_dir)
        made = dedup_generic(ledger, args.workers)
        for row in made:
            print(f"  {row['id']} {ledger.name_of(row['subject'])} → {ledger.name_of(row['object'])} [{row['status']}] {row['why'][:60]}")
        print(f"圣经泛称记录 {len(generic_records(ledger))} 条，判断 {len(made)} 条，合并 {sum(1 for r in made if r['status'] == 'accepted')}")
    elif args.command == "settle":
        ledger = Ledger(novel_dir)
        rows = settle_pending(ledger, args.workers)
        for r in rows:
            print(f"  {r['id']} {r['type']:<13} {r['subject']} → {r['object']} [{r['votes'][0]}/{r['votes'][1]}] → {r['status']} | {r['why'][12:90]}")
        print(f"复判 {len(rows)} 对：{dict(Counter(r['status'] for r in rows))}；仍待确认 {sum(1 for c in ledger.claims if c.get('status') == 'pending')}")
    elif args.command in ("accept", "reject"):
        ok = Ledger(novel_dir).decide(args.claim or "", "accepted" if args.command == "accept" else "rejected", args.note)
        print(f"{args.claim}: 已记为 {args.command} 并写入 corrections.json" if ok else f"{args.claim}: 没有这条判断")
    return 0


if __name__ == "__main__":
    sys.exit(main())
