"""Planning-gate telemetry: every gate firing is one JSONL event, aggregated per gate code.

The event log answers the "did this gate over-block" question without touching any
decision logic.  An event's fatal flag marks how the chapter ended for that firing:

- fatal=True: the chapter failed with this gate in its final verdict.
- fatal=False: the gate fired and the chapter went on - the issue was waived,
  reported only, or repaired in a later attempt.
"""
from __future__ import annotations
import json
import time
from pathlib import Path

EVENTS_NAME = 'gate-events.jsonl'


def record_gate_event(novel_dir, chapter, code, fatal: bool) -> None:
    event = {'ts': round(time.time(), 3), 'chapter': int(chapter), 'code': str(code), 'fatal': bool(fatal)}
    with (Path(novel_dir) / EVENTS_NAME).open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + '\n')


def gate_report(novel_dir) -> dict:
    """Aggregate the event log per gate code.

    Per code: blocked = recorded firings, fatal = firings in a failed chapter's final
    verdict, passed_chapters = chapters that fired this gate and ultimately passed
    (no fatal event for any gate in that chapter).
    """
    path = Path(novel_dir) / EVENTS_NAME
    stats: dict[str, dict] = {}
    failed_chapters: set[int] = set()
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            code = str(event.get('code'))
            chapter = int(event.get('chapter', 0))
            entry = stats.setdefault(code, {'blocked': 0, 'fatal': 0, 'chapters': set()})
            entry['blocked'] += 1
            if event.get('fatal'):
                entry['fatal'] += 1
                failed_chapters.add(chapter)
            else:
                entry['chapters'].add(chapter)
    return {code: {'blocked': entry['blocked'], 'fatal': entry['fatal'],
                   'passed_chapters': len(entry['chapters'] - failed_chapters)}
            for code, entry in stats.items()}
