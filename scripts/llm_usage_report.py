#!/usr/bin/env python
"""Aggregate .runtime/llm-usage.jsonl: tokens per episode per stage/model.

    llm_usage_report.py [--novel X] [--last N]

Local models are not billed per token; this measures efficiency (and where retries burn).
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--novel')
    ap.add_argument('--last', type=int, default=0, help='only the last N rows')
    args = ap.parse_args()
    path = ROOT / '.runtime' / 'llm-usage.jsonl'
    if not path.is_file():
        print('no usage records yet')
        return 0
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines()[-args.last if args.last else None:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    if args.novel:
        rows = [r for r in rows if r.get('novel') == args.novel]
    per = defaultdict(lambda: {'calls': 0, 'in': 0, 'out': 0, 'cache': 0, 'stages': defaultdict(int)})
    for r in rows:
        key = (r.get('novel', '?'), str(r.get('episode', '?')))
        p = per[key]
        p['calls'] += 1
        p['in'] += r.get('prompt_tokens') or r.get('input_tokens') or 0
        p['out'] += r.get('completion_tokens') or r.get('output_tokens') or 0
        p['cache'] += r.get('cache_read_input_tokens') or 0
        p['stages'][r.get('stage', '?')] += 1
    print(f"{'novel':<16} {'episode':<8} {'calls':>5} {'input':>12} {'cache_read':>12} {'output':>9}  stages")
    total = {'calls': 0, 'in': 0, 'out': 0, 'cache': 0}
    for (novel, ep), p in sorted(per.items()):
        stages = ','.join(f'{k}:{v}' for k, v in sorted(p['stages'].items()))
        print(f"{novel:<16} {ep:<8} {p['calls']:>5} {p['in']:>12,} {p['cache']:>12,} {p['out']:>9,}  {stages}")
        for k in total:
            total[k] += p[k]
    print(f"total {total['calls']} calls, in {total['in']:,}, cache {total['cache']:,}, out {total['out']:,}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
