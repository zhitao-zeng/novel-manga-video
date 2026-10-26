"""Chapter discovery must not rescan the host once for every production range."""
from types import SimpleNamespace
from unittest.mock import Mock

from novel_manga.application.production import control, conductor_workers, episode_agents
from novel_manga.application.production.conductor_flow import Conductor


def process(pid, book, *, mode=15):
    args = ['python', 'thin_batch.py', '--novel-dir', str(book),
            '--chapters', '1-1', '--stage', 'render']
    if mode is not None:
        args += ['--plan-mode', str(mode)]
    return {'pid':pid, 'args':args, 'cwd':str(book.parent)}


def test_300_range_queries_share_one_process_read(tmp_path, monkeypatch):
    scan = Mock(return_value=[])
    monkeypatch.setattr(control, 'processes', scan)
    monkeypatch.setattr(conductor_workers.subprocess, 'run',
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout=''))
    c = SimpleNamespace(novel_dir=tmp_path/'book', procs={})
    for n in range(1, 301):
        assert not conductor_workers.external_running(c, f'--chapters {n}-{n} --stage render', 15)
    assert scan.call_count == 1


def test_external_discovery_keeps_book_mode_and_worker_ownership(tmp_path, monkeypatch):
    book = tmp_path/'book'
    rows = [process(11, tmp_path/'other'), process(12, book, mode=30), process(13, book)]
    rows[1]['args'][2:4] = ['--novel-dir='+str(book)]
    rows[2]['args'][3] = book.name
    monkeypatch.setattr(control, 'processes', lambda: rows)
    c = SimpleNamespace(novel_dir=book, procs={'owned':SimpleNamespace(pid=13, poll=lambda:None)})
    pattern = '--chapters 1-1 --stage render'
    assert not conductor_workers.external_running(c, pattern, 15)
    assert conductor_workers.external_running(c, pattern, 30)
    c.procs = {}
    assert conductor_workers.external_running(c, pattern, 15)


def test_next_tick_refreshes_exited_and_new_external_jobs(tmp_path, monkeypatch):
    book = tmp_path/'book'
    rows = [process(20, book, mode=None)]
    monkeypatch.setattr(control, 'processes', lambda: list(rows))
    c = object.__new__(Conductor)
    c.executor, c.novel_dir, c.procs = 'episode_agent', book, {}
    monkeypatch.setattr(episode_agents, 'tick', lambda owner:
        conductor_workers.external_running(owner, '--chapters 1-1 --stage render', 15))
    assert c.tick()
    rows.clear()
    assert not c.tick()
    rows.append(process(21, book))
    assert c.tick()
