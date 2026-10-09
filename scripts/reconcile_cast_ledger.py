#!/usr/bin/env python
"""名单 vs 台账对账:reading_cast 的名单与实体台账逐项核对。

    reconcile_cast_ledger.py --novel-dir outputs/X [--fix]

不报不修的检查:
  1. 名单人物在台账里没有实体(绑定会沿别名走到空处,章节卡死——地牛案例)
  2. same_as 合并的证据是变身/乔装类描述而非"同一人"直陈(许流年在楚天秋案例)

--fix 只采用原始读书中类型、named 和原文证据均明确的缺失记录；其余保持待核对。第 2 类必须人工裁决,
脚本只列出待复核清单,不自动撤销。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import argparse
import json

from novel_manga.application.identity.ledger_store import Ledger
from novel_manga.entities import contracts as entity_contracts
from novel_manga.application.identity.ledger_store import novel_texts, read_json
from novel_manga.application.identity.ledger_extraction import seed_candidates
from novel_manga.application.identity.ledger_flow import adopt_roster


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
    aliases = {**read_json(novel_dir / 'bible_aliases.json', {}), **(roster.get('aliases') or {})}
    aliased = {name for name, target in aliases.items() if target in known}
    unresolved = [n for n in missing if n not in aliased]
    candidates = seed_candidates(ledger, unresolved, novel_texts(novel_dir))
    print(f"名单 {len(roster.get('characters', []))} 项;台账缺 {len(missing)} 项(已有有效别名 {len(aliased & set(missing))} 项):")
    print('  有读书证据可采用: ' + ('、'.join(candidates) or '无'))
    print('  待核对: ' + ('、'.join(n for n in unresolved if n not in candidates) or '无'))

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

    if args.fix and candidates:
        result = adopt_roster(novel_dir, roster)
        unresolved = result['unresolved']
        print(f"\n已采用 {len(result['seeded'])} 项。")
    return 1 if (suspicious or unresolved) else 0


if __name__ == "__main__":
    raise SystemExit(main())
