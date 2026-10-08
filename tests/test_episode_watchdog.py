import json
import os
import time

from scripts.episode_watchdog import ALERTS_FILE, scan


def _write_execution(run, record, age_seconds):
    run.mkdir()
    path = run / 'execution.json'
    path.write_text(json.dumps(record), encoding='utf-8')
    old = time.time() - age_seconds
    os.utime(path, (old, old))
    return path


def test_stale_execution_record_with_dead_pid_is_flagged(tmp_path):
    _write_execution(tmp_path / 'run1', {'phase': 'rendering', 'pid': 99999999, 'at': 'x'}, 3600)
    alerts = scan(tmp_path, stale_minutes=20)
    assert len(alerts) == 1
    assert alerts[0]['phase'] == 'rendering'
    assert alerts[0]['pid'] == 99999999
    assert alerts[0]['run'] == 'run1'
    assert alerts[0]['idle_minutes'] >= 59
    lines = (tmp_path / ALERTS_FILE).read_text(encoding='utf-8').splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved['phase'] == 'rendering'
    assert saved['at']


def test_fresh_execution_record_is_not_flagged(tmp_path):
    _write_execution(tmp_path / 'run2', {'phase': 'rendering', 'pid': 99999999, 'at': 'x'}, 60)
    alerts = scan(tmp_path, stale_minutes=20)
    assert alerts == []
    assert not (tmp_path / ALERTS_FILE).exists()


def test_stale_record_with_live_pid_is_not_flagged(tmp_path):
    _write_execution(tmp_path / 'run3', {'phase': 'rendering', 'pid': os.getpid(), 'at': 'x'}, 3600)
    assert scan(tmp_path, stale_minutes=20) == []


def test_alerts_are_appended_not_overwritten(tmp_path):
    _write_execution(tmp_path / 'run1', {'phase': 'rendering', 'pid': 99999999, 'at': 'x'}, 3600)
    scan(tmp_path, stale_minutes=20)
    scan(tmp_path, stale_minutes=20)
    lines = (tmp_path / ALERTS_FILE).read_text(encoding='utf-8').splitlines()
    assert len(lines) == 2
