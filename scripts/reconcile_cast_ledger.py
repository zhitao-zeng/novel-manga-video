#!/usr/bin/env python
"""名单 vs 台账对账:reading_cast 的名单与实体台账逐项核对。

    reconcile_cast_ledger.py --novel-dir outputs/X [--fix]

不报不修的检查:
  1. 名单人物在台账里没有实体(绑定会沿别名走到空处,章节卡死——地牛案例)
  2. same_as 合并的证据是变身/乔装类描述而非"同一人"直陈(许流年在楚天秋案例)

--fix 只补第 1 类(为缺失者播种 named 实体,外貌仍由读书逐章补);第 2 类必须人工裁决,
脚本只列出待复核清单,不自动撤销。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import argparse
import json

from novel_manga.application.identity.ledger_store import Ledger, ledger_lock
from novel_manga.entities import contracts as entity_contracts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--fix", action="store_true")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()

    roster_path = novel_dir / "reading_cast.json"
    roster = json.loads(roster_path.read_text(encoding="utf-8")) if roster_path.is_file() else {}
    ledger = Ledger(novel_dir)
    known = {ledger.name_of(eid) for eid in ledger.by_id} | set(ledger.by_name)

    missing = [n for n in roster.get("characters", []) if n and n not in known]
    aliased = {a for a in (roster.get("aliases") or {})} | set(json.loads((novel_dir / "bible_aliases.json").read_text(encoding="utf-8"))
                                                              ) if (novel_dir / "bible_aliases.json").is_file() else set()
    seedable = [n for n in missing if n not in aliased]
    print(f"名单 {len(roster.get('characters', []))} 人;台账缺 {len(missing)} 人(其中 {len(aliased & set(missing))} 个是指称别名,由别名解析命中正名,不播种):")
    print("  待播种: " + ("、".join(seedable) if seedable else "无"))

    suspicious = []
    for c in ledger.claims:
        if c.get("type") == "same_as" and c.get("status") == "accepted" \
                and entity_contracts.looks_like_transformation(c.get("evidence", "")):
            suspicious.append((c["id"], ledger.name_of(c["subject"]), ledger.name_of(c["object"]),
                               str(c.get("evidence", ""))[:60]))
    if suspicious:
        print(f"\n变身类证据的已接受合并(待人工复核) {len(suspicious)} 条:")
        for cid, a, b, ev in suspicious:
            print(f"  {cid}: {a} ↔ {b} | {ev}")
    else:
        print("\n变身类证据的已接受合并: 无")

    if args.fix and seedable:
        with ledger_lock(novel_dir):
            ledger = Ledger(novel_dir)
            known = {ledger.name_of(eid) for eid in ledger.by_id} | set(ledger.by_name)
            seedable = [name for name in seedable if name not in known]
            for name in seedable:
                ledger._add_entity(name, "person", True, "读书名单在册(对账播种)", 0)
            ledger.save()
        print(f"\n已补播种 {len(seedable)} 人。")
    return 1 if (suspicious or (seedable and not args.fix)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
