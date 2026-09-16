"""entity_ledger_thin responsibilities; existing evidence and identity policy."""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import argparse
import json
import ledger_flow_thin as ledger_flow
import ledger_resolution_thin as ledger_resolution
import ledger_store_thin as ledger_store
import ledger_views_thin as ledger_views

ROOT = Path(__file__).resolve().parents[1]

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
        texts = ledger_store.novel_texts(novel_dir)
        ledger_flow.run_extract(novel_dir, parse_chapters(args.chapters) if args.chapters else sorted(texts), args.workers, texts)
    elif args.command == "resolve":
        ledger = ledger_store.Ledger(novel_dir)
        chapters = parse_chapters(args.chapters) if args.chapters else sorted(int(p.stem[3:]) for p in (ledger.base / "raw").glob("ch_*.json"))
        ledger_flow.run_resolve(novel_dir, chapters, args.workers)
    elif args.command == "snapshot":
        print(json.dumps(ledger_views.snapshot(novel_dir, args.chapter, args.segments.split(",") if args.segments else None), ensure_ascii=False, indent=1))
    elif args.command == "index":
        index = ledger_views.build_index(novel_dir)
        out = args.out or (novel_dir / "entity_index.json")
        ledger_store.write_json(out, index)
        print(f"{novel_dir.name}: {len(index['characters'])} 条记录，{index['chapters']} 章 → {out}")
    elif args.command == "pending":
        ledger = ledger_store.Ledger(novel_dir)
        rows = [c for c in ledger.claims if c.get("status") == "pending"]
        rows.sort(key=lambda c: (not c.get("priority"), c["chapter"]))  # what changes the picture first: bodies, card holders
        for c in rows:
            print(f"{'★' if c.get('priority') else ' '} ch{c['chapter']} {c['id']} {c['type']}: {ledger.name_of(c['subject'])} → {ledger.name_of(c['object'])} "
                  f"[{c.get('verdict')}] 隐瞒读者={c['hidden_from_reader']} | {(c.get('settled') or c['evidence'])[:70]}")
        print(f"待确认 {len(rows)} 条，其中 ★ 影响画面（换身体或有卡的人物）{sum(1 for c in rows if c.get('priority'))} 条")
        if args.html:
            for page in ledger_views.pending_pages(novel_dir):
                print(f"  {page}")
    elif args.command == "undo":
        print("撤销成功" if ledger_resolution.undo_merge(ledger_store.Ledger(novel_dir), args.claim or "") else "没有这条合并")
    elif args.command == "dedup":
        ledger = ledger_store.Ledger(novel_dir)
        made = ledger_resolution.dedup_generic(ledger, args.workers)
        for row in made:
            print(f"  {row['id']} {ledger.name_of(row['subject'])} → {ledger.name_of(row['object'])} [{row['status']}] {row['why'][:60]}")
        print(f"圣经泛称记录 {len(ledger_resolution.generic_records(ledger))} 条，判断 {len(made)} 条，合并 {sum(1 for r in made if r['status'] == 'accepted')}")
    elif args.command == "settle":
        ledger = ledger_store.Ledger(novel_dir)
        rows = ledger_resolution.settle_pending(ledger, args.workers)
        for r in rows:
            print(f"  {r['id']} {r['type']:<13} {r['subject']} → {r['object']} [{r['votes'][0]}/{r['votes'][1]}] → {r['status']} | {r['why'][12:90]}")
        print(f"复判 {len(rows)} 对：{dict(Counter(r['status'] for r in rows))}；仍待确认 {sum(1 for c in ledger.claims if c.get('status') == 'pending')}")
    elif args.command in ("accept", "reject"):
        ok = ledger_resolution.decide(ledger_store.Ledger(novel_dir), args.claim or "", "accepted" if args.command == "accept" else "rejected", args.note)
        print(f"{args.claim}: 已记为 {args.command} 并写入 corrections.json" if ok else f"{args.claim}: 没有这条判断")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
