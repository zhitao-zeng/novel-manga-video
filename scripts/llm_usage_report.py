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


def normalize_cli_counters(rows):
    """Historical resumed CLI rows contain thread totals; preserve the raw ledger."""
    previous = {}
    result = []
    fields = ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'reasoning_tokens', 'total_tokens')
    for original in rows:
        row = dict(original)
        if (row.get('backend') == 'codex-chatgpt-cli' and row.get('usage_source') == 'codex_cli_turn'
                and row.get('response_id') and row.get('usage_known') is not False):
            thread = row['response_id'].split('/', 1)[0]
            totals = {k: row[k] for k in fields if k in row}
            old = previous.get(thread, {})
            row.update({k: value-old.get(k, 0) for k, value in totals.items()})
            row['usage_source'] = 'codex_cli_turn_delta'
            previous[thread] = totals
        result.append(row)
    return result


def summarize(rows, prices=None):
    """Price reported tokens only; keep missing usage and native ambiguity visible."""
    rows = normalize_cli_counters(rows)
    prices = prices or {}
    result = {'cost_basis': 'token_price_estimate_not_invoice', 'records': len(rows),
        'model_request_records': 0, 'unidentified_agent_attempts': 0, 'unknown_usage_records': 0,
        'cost_unknown_records': 0, 'native_semantics_unverified': 0,
        'estimated_credits_min': 0.0, 'estimated_credits_max': 0.0, 'by_role': {}}
    for row in rows:
        if row.get('event_type') == 'agent_attempt':
            result['unidentified_agent_attempts'] += 1
        else:
            result['model_request_records'] += 1
        role = row.get('name', '?')
        group = result['by_role'].setdefault(role, {'records': 0, 'reported_input_tokens': 0,
                                                   'reported_output_tokens': 0})
        group['records'] += 1
        incoming = row.get('prompt_tokens', row.get('input_tokens'))
        outgoing = row.get('completion_tokens', row.get('output_tokens'))
        group['reported_input_tokens'] += incoming or 0
        group['reported_output_tokens'] += outgoing or 0
        if row.get('usage_known') is False or incoming is None or outgoing is None:
            result['unknown_usage_records'] += 1
            result['cost_unknown_records'] += 1
            continue
        price = prices.get(row.get('model'))
        if price is None or row.get('usage_source') == 'cli_stream_unverified':
            result['cost_unknown_records'] += 1
            continue
        normal = price['input_credits_per_million_tokens']
        output = outgoing*price['output_credits_per_million_tokens']
        cached_price = price['cache_read_credits_per_million_tokens']
        cached = row.get('cache_read_input_tokens')
        if 'prompt_tokens' in row:
            low = incoming*cached_price+output if cached is None else max(0, incoming-cached)*normal+cached*cached_price+output
            high = incoming*normal+output if cached is None else low
        else:
            cached = cached or 0
            low = max(0, incoming-cached)*normal+cached*cached_price+output
            high = incoming*normal+cached*cached_price+output
            result['native_semantics_unverified'] += 1
        result['estimated_credits_min'] += low/1000000
        result['estimated_credits_max'] += high/1000000
    for field in ('estimated_credits_min', 'estimated_credits_max'):
        result[field] = round(result[field], 6)
    result['estimate_covers_all_records'] = result['cost_unknown_records'] == 0 and bool(prices)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--novel')
    ap.add_argument('--last', type=int, default=0, help='only the last N rows')
    ap.add_argument('--since', type=float, default=0, help='only records at/after this Unix timestamp')
    ap.add_argument('--json', action='store_true', help='structured known-usage subtotal and explicit unknowns')
    ap.add_argument('--pricing', type=Path, help='official model pricing snapshot, keyed by requested model ID')
    args = ap.parse_args()
    path = ROOT / '.runtime' / 'llm-usage.jsonl'
    if not path.is_file():
        print('no usage records yet')
        return 0
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    rows = normalize_cli_counters(rows)
    if args.last:
        rows = rows[-args.last:]
    if args.novel:
        rows = [r for r in rows if r.get('novel') == args.novel]
    rows = [r for r in rows if r.get('ts', 0) >= args.since]
    prices = json.loads(args.pricing.read_text()) if args.pricing else {}
    if args.json:
        print(json.dumps(summarize(rows, prices), ensure_ascii=False, indent=2))
        return 0
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
    if any(r.get('usage_known') is False for r in rows):
        print('Some requests have unknown usage; the token total is a reported subtotal, not a complete bill.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
