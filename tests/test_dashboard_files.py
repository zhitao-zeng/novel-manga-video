from pathlib import Path
import json
import os

from novel_manga.dashboard.files import DashboardFiles


def test_collectors_share_directory_discovery_and_unchanged_json(tmp_path, monkeypatch):
    novel = tmp_path / 'book'; episode = novel / 'book_1'; episode.mkdir(parents=True)
    path = episode / 'clip_plan.json'; path.write_text('{"clips": []}')
    cache = DashboardFiles(); scans = []; reads = []
    original_scan, original_read = os.scandir, Path.read_text
    monkeypatch.setattr(os, 'scandir', lambda p: (scans.append(p), original_scan(p))[1])
    monkeypatch.setattr(Path, 'read_text', lambda p, *a, **kw: (reads.append(p), original_read(p, *a, **kw))[1])
    history = cache.scan(novel); repair = cache.scan(novel)
    assert history.directories == repair.directories == (episode,)
    assert history.read(path) == repair.read(path) == {'clips': []}
    assert len(scans) == 1 and reads.count(path) == 1
    previous = path.stat().st_mtime_ns
    path.write_text('{"clips": [{"clip_id": "new"}]}')
    os.utime(path, ns=(previous+1000000, previous+1000000))
    assert cache.scan(novel).read(path)['clips'][0]['clip_id'] == 'new'
    assert reads.count(path) == 2
    second = novel / 'book_2'; second.mkdir()
    assert set(cache.scan(novel).directories) == {episode, second}
    path.unlink()
    assert cache.scan(novel).read(path, {}) == {}


def test_one_pass_stats_shared_files_once_but_next_pass_is_fresh(tmp_path, monkeypatch):
    novel = tmp_path / 'book'; novel.mkdir(); path = novel / 'state.json'; path.write_text('{}')
    files = DashboardFiles(); scan = files.scan(novel); calls = []
    original = Path.stat
    def stat(p, *a, **kw):
        calls.append(p)
        return original(p, *a, **kw)
    monkeypatch.setattr(Path, 'stat', stat)
    scan.mtime(path); scan.take(path); scan.read(path)
    assert calls.count(path) == 1
    files.scan(novel).read(path)
    assert calls.count(path) == 2
