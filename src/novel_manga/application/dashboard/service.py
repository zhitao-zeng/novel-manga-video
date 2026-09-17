"""Compose live and historical snapshots without blocking the HTTP handler."""
from __future__ import annotations
import threading
import time
from novel_manga.dashboard.cache import CachedSnapshot
import novel_manga.application.dashboard.config as config
import novel_manga.application.dashboard.history as history
import novel_manga.application.dashboard.resources as resources
from novel_manga.application.dashboard.metrics import pipeline_metrics
from novel_manga.application.production.control import processes


class DashboardSnapshots:
    def __init__(self, root, novel_ids):
        directory = root / 'outputs/.dashboard'
        scope = ['dashboard-snapshots-v1', *novel_ids]
        self.history = CachedSnapshot(directory / 'history.json', scope, config.BOARD_SECONDS)
        self.status = CachedSnapshot(directory / 'status.json', scope, config.CACHE_SECONDS)

    def collect_history(self):
        return {'now': time.strftime('%F %T'), 'novels': [history._board_novel(n) for n in config.NOVELS]}

    def collect_status(self):
        previous = self.history.peek() or {}
        by_id = {n['id']: n for n in previous.get('novels', [])}
        return {'now': time.strftime('%F %T'),
                'novels': [{**by_id.get(n['id'], {}), 'id': n['id'], 'title': n['title'],
                            'history_loading': n['id'] not in by_id, 'history_at': previous.get('now'),
                            'attention': by_id.get(n['id'], {}).get('attention', [])} for n in config.NOVELS],
                'lanes': resources._lanes(), 'workers': resources._workers(), 'processes': resources._processes(),
                'inflight': resources._inflight(), 'local': resources._local_video_cached(), 'warnings': resources._warnings()}

    def attach_live(self, data):
        # Current controller state is read on every response; historical cache
        # age must never delay a changed delivery count or pause status.
        process_rows = processes()
        rows = [{**n, 'pipeline': pipeline_metrics(config.ROOT / 'outputs' / n['id'], process_rows=process_rows),
                 'history_at': n.get('history_at', data.get('now'))} for n in data.get('novels', [])]
        rows.sort(key=lambda n: 0 if (n.get('pipeline') or {}).get('mode', 'repair') == 'repair' and n.get('pipeline')
                  else 1 if n.get('pipeline') else 2)
        return {**data, 'novels': rows, 'live_at': time.strftime('%F %T'), 'ui_version': config.UI_VERSION}

    def board(self):
        data, updating, error = self.history.read(self.collect_history)
        if data is None:
            data = {'building': True, 'now': None, 'novels': [
                {'id': n['id'], 'title': n['title'], 'history_loading': True} for n in config.NOVELS]}
        return self.attach_live({**data, 'history_refreshing': updating, 'history_error': error})

    def live_page(self):
        data, updating, error = self.status.read(self.collect_status)
        if data is None:
            data = {'now': None, 'novels': [{'id': n['id'], 'title': n['title'], 'history_loading': True} for n in config.NOVELS],
                    'lanes': [], 'workers': [], 'processes': {}, 'inflight': [], 'local': [], 'warnings': []}
        return self.attach_live({**data, 'status_refreshing': updating, 'status_error': error})


_service = None
_service_key = None
_service_lock = threading.Lock()


def snapshots():
    global _service, _service_key
    key = (config.ROOT.resolve(), tuple(n['id'] for n in config.NOVELS))
    with _service_lock:
        if key != _service_key:
            _service = DashboardSnapshots(key[0], list(key[1]))
            _service_key = key
        return _service


def board_snapshot():
    return snapshots().board()


def cached_snapshot():
    return snapshots().live_page()
