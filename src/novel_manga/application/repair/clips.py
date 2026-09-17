#!/usr/bin/env python3
"""Existing episode-repair command. Delegates to flow and managed repair services."""
from __future__ import annotations
from novel_manga.application.configuration import project_root
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import sys
import time
ROOT = project_root()
from novel_manga.application.repair.flow import repair_episode, read, failing_clips


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episodes", default="", help="e.g. 12,48-60; default: every episode whose latest review has story-class must_fix clips")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    if args.episodes:
        wanted: list[int] = []
        for part in args.episodes.split(","):
            a, _, b = part.strip().partition("-")
            if a:
                wanted.extend(range(int(a), int(b or a) + 1))
    else:
        wanted = [int(p.parent.name.rsplit("_", 1)[-1]) for p in novel_dir.glob(f"{novel_dir.name}_*/episode_review.json")
                  if failing_clips(read(p, {}))]
        wanted.sort()
    started = time.time()
    print(f"{novel_dir.name}: {len(wanted)} 集有剧情类必修段，{args.workers} 路修段{'' if args.apply else '（不写入）'}", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        def one(n: int) -> dict:
            try:
                if args.apply:
                    from novel_manga.application.repair.managed import prepare
                    result=prepare(novel_dir/f'{novel_dir.name}_{n}')
                    return {'episode':n,'clips':len(result['changed']),'changed':result['changed'],
                            'failing':len(set(result['changed'])|set(result['blocked'])),
                            'why':'; '.join(f'{cid}: {why}' for cid,why in result['blocked'].items()),**result}
                return repair_episode(novel_dir, n, args.apply)
            except Exception as error:  # noqa: BLE001 - reported per episode, never the whole batch
                return {"episode": n, "clips": 0, "why": f"{type(error).__name__}: {str(error)[:120]}"}
        results = list(pool.map(one, wanted))
    clips = sum(r.get("clips", 0) for r in results)
    failing = sum(r.get("failing", 0) for r in results)
    for r in results:
        if r.get("clips") or r.get("why"):
            print(f"  {novel_dir.name}_{r['episode']}: 修 {r.get('clips', 0)}/{r.get('failing', '?')} 段 {r.get('why', '')}", flush=True)
    print(f"REPAIR RESULT: {len(wanted)} episodes, {clips} clips repaired of {failing} failing, {(time.time() - started) / 60:.0f} min", flush=True)
    return 0
