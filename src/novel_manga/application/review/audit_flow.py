"""Orchestrate existing audit workers over the existing finite queue."""
from __future__ import annotations
import novel_manga.episodes as ep_names
from collections import Counter
import json
import os
from pathlib import Path
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from novel_manga.review import audit_queue

def current(novel: Path, ep: int, cid: str) -> dict | None:
    from novel_manga.application.review.store import current_takes
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
    from novel_manga.application.review.store import current_takes, load_evidence
    from novel_manga.review.reconciliation import evidence_key
    from novel_manga.util import read_json as read
    flash = load_evidence([legacy / 'wy_verify_flash.jsonl', state_dir / 'scan_flash.jsonl'])
    targets, skipped = [], Counter()
    for directory in sorted(novel.glob(f'{novel.name}_*'), key=lambda p: ep_names.episode_order(p.name) if ep_names.is_episode(p.name) else 0):
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
    busy = active_episodes(state_dir)
    if state_dir != novel / 'repair_manager':
        busy |= active_episodes(novel / 'repair_manager')
    if episode in busy:
        return False
    from novel_manga.application.review.store import sync_audit_review
    return sync_audit_review(novel / f'{novel.name}_{episode}', state_dir, episode, local, flash)


def run(novel: Path, state_dir: Path, queue: Path, lane: str, workers: int, *, sync_reviews: bool = False, max_tokens: int | None = None) -> int:
    from novel_manga.application.review.verify import CurrentVerifier
    from novel_manga.util import read_json as read
    from novel_manga.util import atomic_write_json
    audit_queue.recover_abandoned(queue)
    output = state_dir / 'shared_audit' / lane / 'records.jsonl'
    output.parent.mkdir(parents=True, exist_ok=True)
    verifier = CurrentVerifier(novel, output, f'shared_{lane}', workers, repair_advice=False,max_tokens=max_tokens)
    sync_lock=threading.Lock()
    if sync_reviews:
        if lane!='qwen':
            raise ValueError('audit-only review synchronization uses the primary Qwen lane')
        from novel_manga.application.review.store import current_evidence
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
            row = audit_queue.claim(queue, lane, os.getpid(), busy)
            if row is None:
                counts = audit_queue.summary(queue).get('counts', {})
                if not counts.get('pending') and not counts.get('running'):
                    return
                stop.wait(2)
                continue
            before = current(novel, row['episode'], row['clip'])
            expected = (row['video'], json.loads(row['take']))
            if before is None or (before['video'], before['take']) != expected:
                audit_queue.finish(queue, row, 'superseded', {'reason': 'selected take changed; normal repair review owns replacement'})
                continue
            record = verifier.verify((row['episode'], row['clip'], '', 'joint'))
            after = current(novel, row['episode'], row['clip'])
            if before != after or (record.get('video'), record.get('take')) != expected:
                audit_queue.finish(queue, row, 'superseded', {'reason': 'take or clip plan changed during verification'})
            elif 'error' in record:
                audit_queue.finish(queue, row, 'pending' if row['attempts'] < 2 else 'error', record)
            else:
                model=read(Path(row['video']+'.task.json'),{}).get('model')
                if model:
                    record['generation_model']=model
                audit_queue.finish(queue, row, 'done', record)
                if sync_reviews:
                    with sync_lock:
                        merge_evidence(local,record)
                        sync_episode(novel,state_dir,row['episode'],local,flash)

    started = time.monotonic()
    def progress(status: str):
        result={'at':time.strftime('%F %T'),'pid':os.getpid(),'status':status,'lane':lane,'workers':workers,
                'elapsed_seconds':round(time.monotonic()-started),'sync_reviews':sync_reviews,**audit_queue.summary(queue)}
        atomic_write_json(state_dir/f'audit_status_{lane}.json',result)
        return result
    progress('running')
    print(f'{lane}: {workers} workers; shared queue {audit_queue.summary(queue)}', flush=True)
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
    counts = audit_queue.summary(queue)['counts']
    progress('complete_with_errors' if counts.get('error') else 'complete')
    print('done' if not counts.get('error') else 'finished with failed checks', flush=True)
    return 0 if not counts.get('error') else 4
