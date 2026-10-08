"""Optional Agent diagnostics; execution and agent-run records remain authoritative."""
import json
from pathlib import Path
import time

FILE = 'episode_state.jsonl'


def record(run_dir: Path, stage: str, result: str = 'observed', **detail) -> bool:
    """Append after a real operation, without changing its outcome or creating a run."""
    try:
        line = json.dumps({'ts': time.time(), 'stage': stage, 'result': result, **detail},
                          ensure_ascii=False) + '\n'
        with (Path(run_dir) / FILE).open('a', encoding='utf-8') as stream:
            stream.write(line)
        return True
    except (OSError, TypeError, ValueError):
        return False


def last_event(run_dir: Path) -> dict:
    """Read a bounded tail; an interrupted final line does not erase earlier evidence."""
    try:
        with (Path(run_dir) / FILE).open('rb') as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 65536))
            lines = stream.read().splitlines()
    except OSError:
        return {}
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(row, dict) and type(row.get('ts')) in (int, float):
            return row
    return {}
