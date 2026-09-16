"""Share unchanged file contents and directory listings across dashboard collectors.

Each pass has fresh file metadata. Controller state remains outside this cache.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import threading


class DashboardFiles:
    def __init__(self):
        self._directories = {}
        self._parsed = {}
        self._lock = threading.RLock()

    def scan(self, novel: Path):
        novel = Path(novel).resolve()
        try:
            stat = novel.stat()
            stamp = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_nlink)
        except OSError:
            return BookScan(self, novel, ())
        with self._lock:
            cached = self._directories.get(novel)
            if cached is None or cached[0] != stamp:
                with os.scandir(novel) as entries:
                    directories = tuple(Path(e.path) for e in entries if e.is_dir()
                                        and e.name.startswith(novel.name + '_') and e.name.rsplit('_', 1)[-1].isdigit())
                cached = self._directories[novel] = (stamp, directories)
        return BookScan(self, novel, cached[1])

    def parsed(self, path, parser, stat):
        if stat is None:
            return None
        key = (path, parser)
        stamp = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = self._parsed.get(key)
            if cached is None or cached[0] != stamp:
                cached = self._parsed[key] = (stamp, parser(path))
        return cached[1]


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))


class BookScan:
    def __init__(self, files, novel, directories):
        self.files, self.novel, self.directories = files, novel, directories
        self._stats = {}
        self.states = {}

    def stat(self, path):
        path = Path(path)
        if path not in self._stats:
            try:
                self._stats[path] = path.stat()
            except OSError:
                self._stats[path] = None
        return self._stats[path]

    def mtime(self, path):
        stat = self.stat(path)
        return stat.st_mtime if stat else 0.0

    def take(self, path):
        if not path:
            return None
        stat = self.stat(path)
        return [stat.st_ino, stat.st_size, stat.st_mtime_ns] if stat else None

    def parsed(self, path, parser):
        path = Path(path)
        return self.files.parsed(path, parser, self.stat(path))

    def read(self, path, default=None):
        if self.stat(path) is None:
            return default
        try:
            return self.parsed(path, _json)
        except (OSError, ValueError):
            return default
