import json
import os
import time
import pytest

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


@pytest.mark.parametrize('status', ['finished','finished_with_issues','blocked','paused','deferred'])
def test_intentionally_stopped_or_finished_run_is_not_a_dead_worker(tmp_path, status):
    run = tmp_path/'run'
    _write_execution(run, {'phase':'rendering','pid':99999999}, 3600)
    state=run/'agent-run.json'
    state.write_text(json.dumps({'status':status,'pid':99999999}))
    old=time.time()-3600;os.utime(state,(old,old))
    assert scan(tmp_path)==[]


def test_legacy_finished_execution_is_not_a_stall(tmp_path):
    _write_execution(tmp_path/'run', {'phase':'finished','pid':99999999}, 3600)
    assert scan(tmp_path)==[]


def test_restarted_live_agent_takes_precedence_over_old_dead_tool(tmp_path):
    run=tmp_path/'run'
    _write_execution(run, {'phase':'rendering','pid':99999999}, 3600)
    (run/'agent-run.json').write_text(json.dumps({'status':'running','pid':os.getpid()}))
    assert scan(tmp_path)==[]


def test_old_finished_receipt_does_not_hide_a_new_dead_run(tmp_path):
    run=tmp_path/'run'
    _write_execution(run, {'phase':'finished','pid':99999999}, 3600)
    state=run/'agent-run.json'
    state.write_text(json.dumps({'status':'running','phase':'tool_running','pid':99999998}))
    old=time.time()-1800;os.utime(state,(old,old))
    result=scan(tmp_path)
    assert len(result)==1 and result[0]['pid']==99999998
