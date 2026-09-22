#!/usr/bin/env python
"""How the book actually refers to each character: the index every name lookup resolves through.

    build_entity_index.py --novel-dir outputs/X [--apply]

The bible names 薇奥拉公主 once; the chapters say 薇奥拉.  Until 2026-09-13 every stage matched names its own
way - the planner's candidate list, the packer's "who is in the picture", the reviewer's "who was named" -
and each missed the people the prose calls by a short form, which is how 薇奥拉's kiss was given to the cat.
This walks the book (the episodes' segments.json) and records, per bible character, the surface forms that
really occur - the full name, the aliases from bible_aliases.json, the given name before a ·surname, the name
without its title, 小+name - keeping a short form only when it belongs to that one character and sits inside
no other character's name.  With it come the mention count, the first and last chapter, a tier (lead / major
/ minor / extra: the role field is prose in this bible, so the count decides), a generic flag for role nouns
(醉汉, 酒保, 国王) that are as often a common noun as a person, and the card id from the manifest.  Written to
<novel>/entity_index.json; plan_chapter_thin, build_clip_plan_thin and the reviewer read it when present.
Without --apply it only prints.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from novel_manga.application.identity.ledger_store import chapter_texts  # noqa: E402
from novel_manga.story.identity import scan_mentions, short_forms  # noqa: E402

INDEX_POLICY = "entity-index-v1"
GENERIC = re.compile(r"^(醉汉|酒保|国王|王后|侍者|议长|船长|医生|护卫|文员|店员|守夜人|学生|教授|神父|修女|士兵|骑士|男仆|女仆|女佣|管家|车夫|"
                     r"老者|老人|老妇|少年|少女|男子|女子|路人|怪物|尸鬼|骷髅|邪物|女妖|天使|恶魔|巨人|灵魂|神|猫咪|白猫|银猫|老虎|"
                     r"调查师|秘环师|占卜家|术士|女术士|侦探|学者|教士|牧师|侍女|仆人|老板|店主|掌柜|守卫|卫兵|警察|警官|画家|作家|诗人|"
                     r"贵族|绅士|淑女|夫人|太太|先生|小姐|女士|公主|王子)$")
STOP_FORMS = {"那位", "这位", "某位", "一位", "两位", "几位", "各位", "诸位", "小那位", "小这位"}  # a title stripped off a description
MAJOR_TOP = 12       # the most-mentioned characters after the leads
MINOR_MENTIONS = 20  # mentioned this often anywhere in the book: a recurring face


def build(novel_dir: Path) -> dict:
    bible = json.loads((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    names = [c["name"] for c in bible.get("characters", []) if str(c.get("name", "")).strip()]
    roles = {c["name"]: str(c.get("role") or "") for c in bible.get("characters", [])}
    aliases_path = novel_dir / "bible_aliases.json"
    aliases: dict[str, str] = json.loads(aliases_path.read_text(encoding="utf-8")) if aliases_path.is_file() else {}
    manifest_path = novel_dir / "series_assets" / "manifest.json"
    cards: dict[str, str] = {}
    if manifest_path.is_file():
        for row in json.loads(manifest_path.read_text(encoding="utf-8")).get("characters", []):
            if row.get("name") and row.get("asset_id"):
                cards[row["name"]] = row["asset_id"]
    chapters = chapter_texts(novel_dir)
    whole = "\n".join(text for _, text in sorted(chapters.items()))

    candidates: dict[str, set[str]] = {}
    for name in names:
        # An alias counts only when it looks like a name: three characters or more and not a role noun.  The
        # table maps 医生 to 比尔·维克托 and 女仆 to 卡拉太太, which would make every doctor and maid that person.
        table = {a for a, target in aliases.items() if target == name and len(a) >= 3 and not GENERIC.match(a)}
        forms = {name, *table, *short_forms(name)}
        candidates[name] = {f for f in forms if (len(f) >= 2 or f == name) and f not in STOP_FORMS}
    owners: dict[str, set[str]] = defaultdict(set)
    for name, forms in candidates.items():
        for form in forms:
            owners[form].add(name)
    ambiguous: dict[str, list[str]] = {form: sorted(names_) for form, names_ in owners.items() if len(names_) > 1}
    eligible = {name: [f for f in candidates[name] if f == name or f not in ambiguous] for name in names}
    # Count every mention once, longest form first (莱恩·诺克斯·格雷 is not also 莱恩), over the whole book
    # and per chapter for the first/last appearance.
    counts: dict[str, Counter] = {name: Counter() for name in names}
    first_last: dict[str, list] = {name: [None, None] for name in names}
    for index, text in sorted(chapters.items()):
        for _, name, form in scan_mentions(text, eligible):
            counts[name][form] += 1
            span = first_last[name]
            span[0] = index if span[0] is None else span[0]
            span[1] = index

    characters = []
    for name in names:
        usable = dict(sorted(counts[name].items(), key=lambda kv: -kv[1]))
        present = [i for i in first_last[name] if i is not None]
        mentions = sum(usable.values())
        characters.append({
            "name": name, "asset_id": cards.get(name), "role": roles.get(name, ""),
            "has_card": bool(cards.get(name)) and (novel_dir / "series_assets" / "characters" / cards[name] / "turnaround.jpeg").is_file(),
            "generic": bool(GENERIC.match(name)) or len(name) < 2,  # a one-character "name" (我, 天, 什) is a word, not a person
            "forms": dict(sorted(usable.items(), key=lambda kv: -kv[1])),
            "mentions": mentions,
            "first_chapter": min(present) if present else None, "last_chapter": max(present) if present else None,
        })
    by_mentions = sorted((c for c in characters if "主角" not in c["role"] and not c["generic"]), key=lambda c: -c["mentions"])
    major = {c["name"] for c in by_mentions[:MAJOR_TOP]}
    for c in characters:
        c["tier"] = ("extra" if len(c["name"]) < 2 else "lead" if "主角" in c["role"] else "major" if c["name"] in major
                     else "minor" if c["mentions"] >= MINOR_MENTIONS else "extra")
    return {"policy": INDEX_POLICY, "built_at": time.strftime("%Y-%m-%d %H:%M:%S"), "chapters": len(chapters),
            "characters": characters, "ambiguous_forms": ambiguous}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    index = build(novel_dir)
    chars = index["characters"]
    tiers = Counter(c["tier"] for c in chars)
    print(f"{novel_dir.name}: {len(chars)} 个角色，{index['chapters']} 章；等级 {dict(tiers)}；泛称 {sum(c['generic'] for c in chars)}；"
          f"原文里一次都没出现 {sum(1 for c in chars if not c['forms'])}；无卡 {sum(1 for c in chars if not c['has_card'])}")
    print("撞名的短称（不用）:", dict(list(index["ambiguous_forms"].items())[:8]))
    print("提及最多的 12 人及叫法:")
    for c in sorted(chars, key=lambda c: -c["mentions"])[:12]:
        print(f"  {c['name']} [{c['tier']}] {c['mentions']} 次: {c['forms']}")
    if args.apply:
        out = novel_dir / "entity_index.json"
        out.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
        print("→", out)
    return 0
