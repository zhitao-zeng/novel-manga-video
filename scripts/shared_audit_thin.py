"""One remaining audit queue shared by local Qwen and the two Flash workers.

The queue is a finite snapshot. A replaced take is handled by normal post-render
review, not counted as a successful second opinion on the replacement.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from novel_manga.review.audit_queue import connect, initialize, alive, recover_abandoned, claim, finish, summary, export_results

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'src')]


def current(novel: Path, ep: int, cid: str) -> dict | None:
    from review_store_thin import current_takes
    from novel_manga.util import read_json as read
    directory = novel / f'{novel.name}_{ep}'
    plan = read(directory / 'clip_plan.json', {})
    clip = next((c for c in plan.get('clips', []) if c['clip_id'] == cid), None)
    if clip is None:
        return None
    take = current_takes(directory, plan, read(directory / 'episode_review.json', {})).get(cid)
    return {**take, 'plan_clip': clip} if take else None


def active_episodes(state_dir: Path) -> set[int]:
    from novel_manga.util import read_json as read
    state = read(state_dir / 'state.json', {})
    return {n for j in state.get('jobs', []) if j['kind'] not in ('scan',)
            and j['status'] in ('pending', 'running') for n in j.get('episodes', [])}


def build_remaining(novel: Path, state_dir: Path, legacy: Path) -> tuple[list[dict], dict]:
    from review_store_thin import current_takes, load_evidence
    from novel_manga.review.reconciliation import evidence_key
    from novel_manga.util import read_json as read
    flash = load_evidence([legacy / 'wy_verify_flash.jsonl', state_dir / 'scan_flash.jsonl'])
    targets, skipped = [], Counter()
    for directory in sorted(novel.glob(f'{novel.name}_*'), key=lambda p: int(p.name.rsplit('_', 1)[-1]) if p.name.rsplit('_', 1)[-1].isdigit() else 0):
        suffix = directory.name.rsplit('_', 1)[-1]
        if not suffix.isdigit():
            continue
        ep = int(suffix)
        plan, review = read(directory / 'clip_plan.json', {}), read(directory / 'episode_review.json', {})
        takes = current_takes(directory, plan, review)
        for clip in plan.get('clips', []):
            if clip.get('kind') != 'video':
                continue
            cid = clip['clip_id']
            take = takes.get(cid)
            verdict = review.get('clips', {}).get(cid) or {}
            if not take:
                skipped['no_take'] += 1
            elif evidence_key(ep, cid, take['video'], take['take']) in flash:
                skipped['already_flash_checked'] += 1
            elif verdict.get('technical') or (verdict.get('tier') or verdict.get('fix_tier')) == 'must_fix':
                skipped['known_error_in_repair'] += 1
            else:
                targets.append({'episode': ep, 'clip': cid, **take})
    return targets, dict(skipped)


def sync_episode(novel: Path, state_dir: Path, episode: int, local: dict, flash: dict) -> bool:
    """An audit-only run updates the board without starting a repair manager."""
    from review_store_thin import reconcile
    from novel_manga.util import read_json as read
    from novel_manga.review.reconciliation import evidence_key, assemble_review
    from novel_manga.util import atomic_write_json
    busy=active_episodes(state_dir)
    if state_dir != novel/'repair_manager':
        busy |= active_episodes(novel/'repair_manager')
    if episode in busy:
        return False
    directory=novel/f'{novel.name}_{episode}'
    original=directory/'episode_review.json'
    backup=state_dir/'before_reviews'/f'{episode}.json'
    if original.is_file() and not backup.exists():
        backup.parent.mkdir(parents=True,exist_ok=True)
        backup.write_bytes(original.read_bytes())
    previous=read(original,{})
    result,takes=reconcile(directory,local,flash,write=False)
    checked={cid for cid,take in takes.items() if evidence_key(episode,cid,take['video'],take['take']) in local}
    clips=dict(previous.get('clips',{}))
    clips.update({cid:row for cid,row in result.get('clips',{}).items() if cid in checked})
    updated=assemble_review(directory,previous,clips)
    updated['audit_scope']={'source':'qwen','checked_clips':sorted(checked)}
    if updated!=previous:
        atomic_write_json(original,updated)
    return True


def run(novel: Path, state_dir: Path, queue: Path, lane: str, workers: int, *, sync_reviews: bool = False, max_tokens: int | None = None) -> int:
    from repair_review_thin import CurrentVerifier
    from novel_manga.util import read_json as read
    from novel_manga.util import atomic_write_json
    recover_abandoned(queue)
    output = state_dir / 'shared_audit' / lane / 'records.jsonl'
    output.parent.mkdir(parents=True, exist_ok=True)
    verifier = CurrentVerifier(novel, output, f'shared_{lane}', workers, repair_advice=False,max_tokens=max_tokens)
    sync_lock=threading.Lock()
    if sync_reviews:
        if lane!='qwen':
            raise ValueError('audit-only review synchronization uses the primary Qwen lane')
        from review_store_thin import current_evidence
        from novel_manga.review.reconciliation import merge_evidence
        local,flash=current_evidence(state_dir/'legacy',state_dir)
        for episode in sorted({key[0] for key in local}):
            sync_episode(novel,state_dir,episode,local,flash)
    # Separate frame directories prevent Flash/local work from overwriting each
    # other's extracted images. Verification never writes episode reviews here.
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *args: stop.set())
    signal.signal(signal.SIGINT, lambda *args: stop.set())

    def work():
        while not stop.is_set():
            busy=active_episodes(state_dir)
            if state_dir != novel/'repair_manager':
                busy |= active_episodes(novel/'repair_manager')
            row = claim(queue, lane, os.getpid(), busy)
            if row is None:
                counts = summary(queue).get('counts', {})
                if not counts.get('pending') and not counts.get('running'):
                    return
                stop.wait(2)
                continue
            before = current(novel, row['episode'], row['clip'])
            expected = (row['video'], json.loads(row['take']))
            if before is None or (before['video'], before['take']) != expected:
                finish(queue, row, 'superseded', {'reason': 'selected take changed; normal repair review owns replacement'})
                continue
            record = verifier.verify((row['episode'], row['clip'], '', 'joint'))
            after = current(novel, row['episode'], row['clip'])
            if before != after or (record.get('video'), record.get('take')) != expected:
                finish(queue, row, 'superseded', {'reason': 'take or clip plan changed during verification'})
            elif 'error' in record:
                finish(queue, row, 'pending' if row['attempts'] < 2 else 'error', record)
            else:
                model=read(Path(row['video']+'.task.json'),{}).get('model')
                if model:
                    record['generation_model']=model
                finish(queue, row, 'done', record)
                if sync_reviews:
                    with sync_lock:
                        merge_evidence(local,record)
                        sync_episode(novel,state_dir,row['episode'],local,flash)

    started = time.monotonic()
    def progress(status: str):
        result={'at':time.strftime('%F %T'),'pid':os.getpid(),'status':status,'lane':lane,'workers':workers,
                'elapsed_seconds':round(time.monotonic()-started),'sync_reviews':sync_reviews,**summary(queue)}
        atomic_write_json(state_dir/f'audit_status_{lane}.json',result)
        return result
    progress('running')
    print(f'{lane}: {workers} workers; shared queue {summary(queue)}', flush=True)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work) for _ in range(workers)]
        while not all(f.done() for f in futures):
            for f in futures:
                if f.done() and f.exception():
                    stop.set()
                    raise f.exception()
            if stop.is_set():
                time.sleep(.2)
            else:
                print(json.dumps(progress('running'), ensure_ascii=False), flush=True)
                stop.wait(20)
        for f in futures:
            f.result()
    if stop.is_set():
        progress('stopped')
        print('stopped after current requests', flush=True)
        return 2
    counts = summary(queue)['counts']
    progress('complete_with_errors' if counts.get('error') else 'complete')
    print('done' if not counts.get('error') else 'finished with failed checks', flush=True)
    return 0 if not counts.get('error') else 4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--novel-dir', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--queue', type=Path, required=True)
    parser.add_argument('--lane', choices=['qwen', 'flash'], required=True)
    parser.add_argument('--workers', type=int, required=True)
    parser.add_argument('--sync-reviews',action='store_true',help='import primary audit results into episode reviews without rendering')
    parser.add_argument('--max-tokens',type=int)
    args = parser.parse_args()
    raise SystemExit(run(args.novel_dir.resolve(), args.state_dir.resolve(), args.queue.resolve(), args.lane, args.workers,
                         sync_reviews=args.sync_reviews,max_tokens=args.max_tokens))


if __name__ == '__main__':
    main()
