"""Actual concurrent ledger writers load fresh state only after acquiring the book lock."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from novel_manga.application.identity import ledger_store as store


def test_lock_excludes_another_descriptor_and_releases_on_failure(tmp_path):
    with pytest.raises(RuntimeError):
        with store.ledger_lock(tmp_path):
            with (tmp_path / 'entity/ledger.lock').open('a') as probe:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            raise RuntimeError('writer failed')
    with (tmp_path / 'entity/ledger.lock').open('a') as probe:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_simultaneous_writers_preserve_both_entities(tmp_path):
    script = '''
from pathlib import Path
import sys, time
from novel_manga.application.identity.ledger_store import Ledger, ledger_lock
book, name = Path(sys.argv[1]), sys.argv[2]
(book / (name + '.ready')).write_text('ready')
while not (book / 'start').exists():
    time.sleep(.01)
with ledger_lock(book):
    ledger = Ledger(book)
    ledger._add_entity(name, 'person', True, 'test', 1)
    time.sleep(.1)
    ledger.save()
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(store.__file__).parents[3])}
    children = [subprocess.Popen([sys.executable, '-c', script, str(tmp_path), name], env=env)
                for name in ('甲', '乙')]
    try:
        deadline = time.monotonic() + 10
        while not all((tmp_path / (name + '.ready')).exists() for name in ('甲', '乙')):
            assert time.monotonic() < deadline
            time.sleep(.01)
        (tmp_path / 'start').touch()
        assert [p.wait(timeout=10) for p in children] == [0, 0]
    finally:
        for p in children:
            if p.poll() is None:
                p.kill()
            p.wait()
    rows = store.read_json(tmp_path / 'entity/entities.json', [])
    assert {r['canonical'] for r in rows} == {'甲', '乙'}
    assert len({r['id'] for r in rows}) == 2


def test_reading_finish_does_not_overwrite_another_writer(tmp_path, monkeypatch):
    from novel_manga.application.planning import reading
    source = tmp_path / 'book.txt'
    source.write_text('第一章 开始\n甲在场。')
    (tmp_path / 'novel.json').write_text(json.dumps({'source': str(source)}))
    (tmp_path / 'story_bible.json').write_text(json.dumps({'characters': [], 'locations': []}))
    (tmp_path / 'recap.json').write_text(json.dumps([{'chapter': 1}]))
    monkeypatch.setattr(reading.ledger_extraction, 'extract', lambda *a: {'chapter': 1})
    def resolve(ledger, *args, **kwargs):
        ledger._add_entity('甲', 'person', True, '', 1)
        ledger.save()
        return {'new': [], 'claims': {}}
    monkeypatch.setattr(reading.ledger_resolution, 'resolve_chapter', resolve)
    monkeypatch.setattr(reading, 'scan_chapter', lambda *a, **kw: {'names': []})
    def grow(*args, **kwargs):
        # This is outside the chapter's ledger lock; simulate another actual writer.
        with store.ledger_lock(tmp_path):
            current = store.Ledger(tmp_path)
            current._add_entity('乙', 'person', True, '', 1)
            current.save()
        return {}
    monkeypatch.setattr(reading, 'grow_bible', grow)
    monkeypatch.setattr(sys, 'argv', ['story_pass', '--novel-dir', str(tmp_path),
                                   '--min-chapter-chars', '1', '--chapters', '1'])
    assert reading.main() == 0
    assert {r['canonical'] for r in store.Ledger(tmp_path).entities} == {'甲', '乙'}
