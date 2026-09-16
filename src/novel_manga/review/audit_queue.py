"""Storage for the existing finite audit snapshot; no verifier or scheduler imports."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager

@contextmanager
def connect(path: Path):
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize(path: Path, targets: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS checks (id INTEGER PRIMARY KEY, episode INTEGER, clip TEXT, '
                   'video TEXT, take TEXT, status TEXT DEFAULT "pending", lane TEXT, pid INTEGER, '
                   'attempts INTEGER DEFAULT 0, result TEXT, UNIQUE(episode, clip, video, take))')
        db.executemany('INSERT OR IGNORE INTO checks(episode,clip,video,take) VALUES (?,?,?,?)',
                       [(r['episode'], r['clip'], r['video'], json.dumps(r['take'])) for r in targets])


def alive(pid: int) -> bool:
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except OSError:
        return False


def recover_abandoned(path: Path):
    with connect(path) as db:
        for row in db.execute('SELECT DISTINCT pid FROM checks WHERE status="running"').fetchall():
            if not alive(row['pid']):
                db.execute('UPDATE checks SET status="pending", pid=NULL WHERE status="running" AND pid=?', (row['pid'],))


def claim(path: Path, lane: str, pid: int, busy: set[int]) -> dict | None:
    with connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        # Episodic rewrites own their files; audit another episode in the meantime.
        where = ' AND episode NOT IN (' + ','.join('?' for _ in busy) + ')' if busy else ''
        row = db.execute('SELECT * FROM checks WHERE status="pending"' + where + ' ORDER BY id LIMIT 1', tuple(busy)).fetchone()
        if row is None:
            return None
        db.execute('UPDATE checks SET status="running",lane=?,pid=?,attempts=attempts+1 WHERE id=?', (lane, pid, row['id']))
        return {**dict(row), 'attempts': row['attempts'] + 1, 'lane': lane}


def finish(path: Path, row: dict, status: str, record: dict):
    with connect(path) as db:
        db.execute('UPDATE checks SET status=?,result=?,pid=NULL WHERE id=?', (status, json.dumps(record, ensure_ascii=False), row['id']))


def summary(path: Path) -> dict:
    if not path.is_file():
        return {}
    with connect(path) as db:
        counts = dict(db.execute('SELECT status,COUNT(*) FROM checks GROUP BY status').fetchall())
        lanes = [dict(r) for r in db.execute('SELECT lane,status,COUNT(*) AS count FROM checks WHERE lane IS NOT NULL GROUP BY lane,status')]
    return {'total': sum(counts.values()), 'counts': counts, 'lanes': lanes}


def export_results(path: Path, lane: str) -> list[dict]:
    """The database result and completion are one transaction, so restart loses neither."""
    with connect(path) as db:
        return [json.loads(r['result']) for r in db.execute('SELECT result FROM checks WHERE status="done" AND lane=?', (lane,))]
