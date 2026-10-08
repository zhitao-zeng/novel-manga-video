"""Record gate firings separately from explicit outcomes of each planning run.

Counts describe attempts and outcomes, not a measured false-positive rate.
Interrupted runs and old records without outcomes do not establish success.
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path

EVENTS_NAME = 'gate-events.jsonl'


def _append(novel_dir, event):
    with (Path(novel_dir) / EVENTS_NAME).open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({'ts':round(time.time(),3), **event}, ensure_ascii=False) + '\n')


def begin_gate_run(novel_dir, chapter, *, episode=None) -> str:
    run_id = f'{os.getpid()}-{time.time_ns()}'
    _append(novel_dir, {'kind':'run','run_id':run_id,'chapter':int(chapter),
                       'episode':str(episode if episode is not None else chapter)})
    return run_id


def record_gate_event(novel_dir, chapter, code, *, run_id, attempt) -> None:
    _append(novel_dir, {'kind':'firing','run_id':run_id,'chapter':int(chapter),
                       'attempt':int(attempt),'code':str(code)})


def finish_gate_run(novel_dir, chapter, run_id, *, passed, fatal_codes=()) -> None:
    _append(novel_dir, {'kind':'outcome','run_id':run_id,'chapter':int(chapter),
                       'passed':bool(passed),'fatal_codes':sorted(set(fatal_codes))})


def gate_report(novel_dir) -> dict:
    """Aggregate the event log per gate code.

    blocked = actual firings; fatal = failed runs naming this gate in their final
    verdict; passed_chapters = chapters that encountered the gate and whose latest
    recorded plans (including separate parts) explicitly passed. Final outcome
    records never count as another firing.
    """
    path = Path(novel_dir) / EVENTS_NAME
    stats: dict[str, dict] = {}
    runs, latest, outcomes = {}, {}, {}
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            run_id = event.get('run_id')
            if event.get('kind') == 'run':
                runs[run_id] = event
                latest[(event['chapter'], event['episode'])] = run_id
            elif run_id in runs and event.get('kind') == 'firing':
                code, chapter = str(event['code']), runs[run_id]['chapter']
                entry = stats.setdefault(code, {'blocked':0,'fatal':0,'chapters':set()})
                entry['blocked'] += 1
                entry['chapters'].add(chapter)
            elif run_id in runs and event.get('kind') == 'outcome':
                outcomes[run_id] = event
    for outcome in outcomes.values():
        if outcome['passed'] is False:
            for code in outcome['fatal_codes']:
                entry = stats.setdefault(code, {'blocked':0,'fatal':0,'chapters':set()})
                entry['fatal'] += 1
    current = {}
    for (chapter, _), run_id in latest.items():
        current.setdefault(chapter, []).append(outcomes.get(run_id, {}).get('passed') is True)
    passed = {chapter for chapter, statuses in current.items() if all(statuses)}
    return {code: {'blocked': entry['blocked'], 'fatal': entry['fatal'],
                   'passed_chapters': len(entry['chapters'] & passed)}
            for code, entry in stats.items()}
