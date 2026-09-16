"""Episode step orchestration and the existing bounded preparation subprocess loop."""
from __future__ import annotations

from pathlib import Path
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from novel_manga.planning.preparation import POLICY, TERMINAL
from preparation_store_thin import read, inputs, record, has_video, backup, eligible, summary
import preparation_actions_thin as actions
ROOT = Path(__file__).resolve().parents[1]

def prepare_one(directory):
    from clip_readiness import inspect_episode
    previous = read(directory.parent / 'h3_preparation/episodes' / (directory.name.rsplit('_', 1)[-1] + '.json'), {})
    attempts = previous.get('attempts', 0) if previous.get('policy') == POLICY and previous.get('inputs') == inputs(directory) else 0
    record(directory, 'starting', attempts=attempts + 1)
    if has_video(directory):
        return record(directory, 'existing_video')
    admitted = read(directory.parent / 'repair_manager/state.json', {}).get('admitted_episodes', [])
    if int(directory.name.rsplit('_', 1)[-1]) in admitted:
        return record(directory, 'production_owned')
    backup(directory)
    source_blocks = read(directory.parent / 'h3_preparation/source_blocks.json', {})
    n = directory.name.rsplit('_', 1)[-1]
    if n in source_blocks:
        return record(directory, 'needs_source', reason=source_blocks[n])
    plan = actions.prepare_plan(directory)
    answer, stopped = actions.check_script(directory, plan)
    if stopped is not None:
        return stopped
    plan = actions.refresh_entity_bindings(directory)
    plan, blocked = inspect_episode(directory)
    if blocked:
        return record(directory, 'needs_replan', blocks=blocked)
    actions.ensure_cards(directory, plan)
    return actions.translate_and_check(directory, plan, answer)


def run(args):
    from novel_manga.util import load_dotenv
    load_dotenv(ROOT / '.env')
    # This queue uses local Qwen, independent of paid planner lane settings.
    os.environ['QWEN38_LOCAL_BASE_URL'] = ','.join(f'http://127.0.0.1:{p}/v1' for p in range(18120, 18125)) + ',http://172.28.4.52:18125/v1,http://172.28.4.52:18126/v1'
    os.environ['QWEN38_LOCAL_MODEL'] = 'Qwen3.8-27B-Project'
    os.environ['QWEN38_LOCAL_API_KEY_VAR'] = 'H3_PROMPT_NO_KEY'
    os.environ['NOVEL_CLIP_SECONDS_MAX'] = '15'
    novel = args.novel_dir.resolve()
    out = novel / 'h3_preparation'
    (out / 'episodes').mkdir(parents=True, exist_ok=True)
    if args.episode is not None:
        directory = novel / f'{novel.name}_{args.episode}'
        try:
            prepare_one(directory)
        except Exception as error:
            from novel_manga.story.source_identity import UnreadableSource
            record(directory, 'needs_source' if isinstance(error, UnreadableSource) else 'error',
                   reason=str(error)[:300] if isinstance(error, ValueError) else type(error).__name__)
            raise
        return
    from build_h3_prompts import episode_numbers
    chapters = sorted(episode_numbers(args.chapters))
    with (out / 'run.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = False
        def pause(*unused):
            nonlocal stop
            stop = True
        signal.signal(signal.SIGTERM, pause)
        signal.signal(signal.SIGINT, pause)
        queue = []
        retries = {}
        for n in chapters:
            row = read(out / 'episodes' / f'{n}.json', {})
            directory = novel / f'{novel.name}_{n}'
            if eligible(row, directory):
                if row.get('policy') == POLICY and row.get('retry_after', 0) > time.time():
                    retries[n] = row['retry_after']
                else:
                    queue.append(n)
        # Revisit known failures under the new policy before untouched chapters.
        queue.sort(key=lambda n: (read(out / 'episodes' / f'{n}.json', {}).get('status', 'pending') == 'pending', n))
        active = {}
        while queue or active or retries:
            due = [n for n, at in retries.items() if at <= time.time()]
            queue[:0] = due
            for n in due:
                retries.pop(n)
            while queue and len(active) < args.workers and not stop:
                n = queue.pop(0)
                log = (out / f'chapter-{n}.log').open('a')
                child = subprocess.Popen([sys.executable, str(ROOT / 'scripts/prepare_h3_book.py'), '--novel-dir', str(novel),
                                          '--episode', str(n)], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL)
                log.close()
                active[n] = child
            for n, child in list(active.items()):
                if child.poll() is not None:
                    row = read(out / 'episodes' / f'{n}.json', {})
                    if child.returncode and row.get('status') not in TERMINAL:
                        record(novel / f'{novel.name}_{n}', 'error', reason=f'worker exited {child.returncode}')
                        row = read(out / 'episodes' / f'{n}.json', {})
                    if row.get('retry_after') and row.get('attempts', 0) < 3:
                        retries[n] = row['retry_after']
                    active.pop(n)
            result = summary(novel, chapters, 'pausing' if stop else 'running', {n:p.pid for n,p in active.items()}, workers=args.workers)
            print(json.dumps({k:v for k,v in result.items() if k!='chapters'}, ensure_ascii=False), flush=True)
            if stop and not active:
                break
            time.sleep(5)
        summary(novel, chapters, 'paused' if stop else 'finished', workers=args.workers)


