"""Serve the last completed snapshot while rebuilding it outside HTTP requests."""
from __future__ import annotations
import json
import threading
import time
from pathlib import Path
from typing import Callable
from novel_manga.util import atomic_write_json


class CachedSnapshot:
    def __init__(self, path: Path, scope: list[str], ttl: float):
        self.path, self.scope, self.ttl = path, list(scope), ttl
        self.data = None
        self.at = 0.0
        self.building = False
        self.error = None
        self.lock = threading.Lock()
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            if saved['scope'] == self.scope:
                self.data, self.at = saved['data'], float(saved['at'])
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def peek(self):
        with self.lock:
            return self.data

    def read(self, produce: Callable[[], dict]):
        with self.lock:
            if (self.data is None or time.time() - self.at > self.ttl) and not self.building:
                self.building = True
                threading.Thread(target=self._refresh, args=(produce,), daemon=True, name='dashboard-snapshot').start()
            return self.data, self.building, self.error

    def _refresh(self, produce: Callable[[], dict]):
        try:
            data = produce()
            at = time.time()
            with self.lock:
                self.data, self.at, self.error = data, at, None
            atomic_write_json(self.path, {'scope': self.scope, 'at': at, 'data': data})
        except Exception as error:
            with self.lock:
                self.error = type(error).__name__
        finally:
            with self.lock:
                self.building = False
