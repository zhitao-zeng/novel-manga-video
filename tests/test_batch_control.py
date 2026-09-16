import json
from pathlib import Path

import pytest

from novel_manga import batch_control as control
from novel_manga.util import atomic_write_json
import pipeline
import pipeline_dashboard as dashboard


def book(tmp_path):
    spec = {'id': 'book', 'title': '测试', 'active': True, 'tmp_dir': 'runtime/conductor',
            'ranges': [{'chapters': '1-3', 'plan_mode': 15}]}
    novel = tmp_path / 'outputs/book'
    novel.mkdir(parents=True)
    return spec, novel


def owner(root, flow, pid=123):
    return {'pid': pid, 'cwd': str(root), 'args': ['python', f'scripts/{control.ENTRIES[flow]}',
            *(['--novel', 'book'] if flow == 'production' else ['--novel-dir', 'outputs/book'])]}


def test_paused_manager_is_not_reported_running_and_status_is_read_only(tmp_path, monkeypatch):
    spec, novel = book(tmp_path)
    state = {'pid': 123, 'status': 'running', 'updated_at': '2026-09-16 17:00:00', 'jobs': [
        {'status': 'pending', 'episodes': [1, 2]}, {'status': 'waiting_plan', 'episodes': [2, 3],
         'reason': '缺少角色资产'}]}
    path = novel / 'repair_manager/state.json'
    atomic_write_json(path, state); (path.parent / 'pause').touch()
    before = {p: p.read_bytes() for p in novel.rglob('*') if p.is_file()}
    monkeypatch.setattr(control, 'processes', lambda: [owner(tmp_path, 'repair')])
    row = control.snapshot(tmp_path, {'novels': [spec]})['novels'][0]['flows']['repair']
    assert row['status'] == 'paused' and row['controller_alive']
    assert row['pending'] == 3 and row['blocked'] == 2
    assert row['blocked_reasons'] == ['缺少角色资产']
    assert before == {p: p.read_bytes() for p in novel.rglob('*') if p.is_file()}


def test_duplicate_start_resumes_existing_repair_without_spawning(tmp_path, monkeypatch):
    spec, novel = book(tmp_path)
    pause = novel / 'repair_manager/pause'; pause.parent.mkdir(); pause.touch()
    monkeypatch.setattr(control, 'processes', lambda: [owner(tmp_path, 'repair')])
    monkeypatch.setattr(control, 'spawn', lambda *a: pytest.fail('duplicate controller'))
    assert control.control(tmp_path, spec, 'repair', 'start')['action'] == 'resumed'
    assert not pause.exists()
    assert control.control(tmp_path, spec, 'repair', 'start')['pids'] == [123]


@pytest.mark.parametrize('flow', ['production', 'prepare'])
def test_stop_signals_only_selected_controller_and_preserves_workers(tmp_path, monkeypatch, flow):
    spec, novel = book(tmp_path); calls = []
    monkeypatch.setattr(control, 'processes', lambda: [owner(tmp_path, flow), owner(tmp_path / 'other', flow, 999)])
    monkeypatch.setattr(control.os, 'kill', lambda pid, sig: calls.append((pid, sig)))
    control.control(tmp_path, spec, flow, 'stop')
    assert calls == [(123, control.signal.SIGTERM)]


def test_repair_stop_uses_native_pause_and_keeps_history(tmp_path, monkeypatch):
    spec, novel = book(tmp_path)
    state = {'jobs': [{'status': 'running', 'pid': 456}], 'passes': {'1': 2}}
    atomic_write_json(novel / 'repair_manager/state.json', state)
    monkeypatch.setattr(control, 'processes', lambda: [owner(tmp_path, 'repair')])
    monkeypatch.setattr(control.os, 'kill', lambda *a: pytest.fail('worker killed'))
    control.control(tmp_path, spec, 'repair', 'stop')
    assert (novel / 'repair_manager/pause').exists()
    assert control.read(novel / 'repair_manager/state.json') == state


def test_preparation_restart_reuses_scope_and_does_not_dispatch_during_drain(tmp_path, monkeypatch):
    spec, novel = book(tmp_path); calls = []
    path = novel / 'h3_preparation/status.json'
    state = {'status': 'pausing', 'chapters': [10, 11], 'workers': 3, 'running': {'10': 456}}
    atomic_write_json(path, state)
    monkeypatch.setattr(control, 'processes', lambda: [])
    monkeypatch.setattr(control, 'alive', lambda pid: pid == 456)
    monkeypatch.setattr(control, 'spawn', lambda root, command, log: calls.append(command) or 789)
    with pytest.raises(ValueError, match='在途任务'):
        control.control(tmp_path, spec, 'prepare', 'start')
    assert not calls
    monkeypatch.setattr(control, 'alive', lambda pid: False)
    control.control(tmp_path, spec, 'prepare', 'start')
    assert control.argument(calls[0], '--chapters') == '10,11'
    assert control.argument(calls[0], '--workers') == '3'
    assert control.read(path) == state


def test_repair_restart_keeps_preparation_gate_scope_and_retry_history(tmp_path, monkeypatch):
    spec, novel = book(tmp_path); calls = []
    state = {'status': 'paused', 'jobs': [], 'scope': {'episodes': [1, 3]},
             'preparation_gate': True, 'model_workers': 4, 'passes': {'1': 2}}
    path = novel / 'repair_manager/state.json'; atomic_write_json(path, state)
    (path.parent / 'pause').touch()
    monkeypatch.setattr(control, 'processes', lambda: [])
    monkeypatch.setattr(control, 'spawn', lambda root, command, log: calls.append(command) or 789)
    control.control(tmp_path, spec, 'repair', 'start')
    assert '--prepared-only' in calls[0] and control.argument(calls[0], '--model-workers') == '4'
    assert control.read(path) == state and not (path.parent / 'pause').exists()


def test_failed_start_preserves_pause(tmp_path, monkeypatch):
    spec, novel = book(tmp_path)
    pause = novel / 'repair_manager/pause'; pause.parent.mkdir(); pause.touch()
    monkeypatch.setattr(control, 'processes', lambda: [])
    def fail(*args):
        raise OSError('cannot start')
    monkeypatch.setattr(control, 'spawn', fail)
    with pytest.raises(OSError):
        control.control(tmp_path, spec, 'repair', 'start')
    assert pause.exists()


def test_dashboard_and_cli_share_same_flow_status(tmp_path, monkeypatch, capsys):
    spec, novel = book(tmp_path)
    state = {'status': 'paused', 'jobs': [], 'summary': {'deliverable_precise': 1, 'total': 3}}
    atomic_write_json(novel / 'repair_manager/state.json', state)
    monkeypatch.setattr(control, 'processes', lambda: [])
    monkeypatch.setattr(pipeline, 'ROOT', tmp_path)
    monkeypatch.setattr(pipeline, 'load', lambda: {'novels': [spec]})
    monkeypatch.setattr(dashboard, 'operation_metrics', lambda path, repair_state=None, process_rows=None:
                        control.flow_snapshot(tmp_path, spec, repair_state=repair_state, rows=process_rows))
    assert pipeline.main(['status', '--novel', 'book', '--json']) == 0
    cli = json.loads(capsys.readouterr().out)['novels'][0]['flows']
    assert dashboard.pipeline_metrics(novel)['flows'] == cli
    assert pipeline.main(['status', '--flow', 'repair', '--json']) == 0
    assert set(json.loads(capsys.readouterr().out)['novels'][0]['flows']) == {'repair'}


def test_start_defaults_to_production_without_a_new_scheduler(tmp_path, monkeypatch):
    spec, novel = book(tmp_path); calls = []
    monkeypatch.setattr(pipeline, 'load', lambda: {'novels': [spec]})
    monkeypatch.setattr(pipeline, 'validate', lambda config: [])
    monkeypatch.setattr(pipeline, 'control', lambda root, book, flow, action, **kw:
                        calls.append((flow, action)) or {'action': 'started'})
    assert pipeline.main(['start', '--novel', 'book']) == 0
    assert calls == [('production', 'start')]


def test_paused_ui_shows_shared_flow_state_without_stale_running_warning():
    import subprocess
    source = Path(__file__).resolve().parents[1] / 'scripts/pipeline_dashboard.js'
    code = '''const fs=require('fs'),vm=require('vm');const c={};vm.createContext(c);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
const p={mode:'operations',flows:{repair:{status:'paused',controller_alive:true,in_flight:0,pending:12,blocked:0}}};
const html=c.pipelineSummary({pipeline:p});
if(!html.includes('已暂停')||!html.includes('管理器存活')||!html.includes('12 集'))throw Error(html);
if(html.includes('NaN')||html.includes('undefined'))throw Error(html);
const n={title:'book',pipeline:{mode:'repair',status:'paused',controller_alive:true,age_seconds:999}};
if(c.pipelineHealth(n)!=='ok'||c.pipelineAlerts([n]).includes('需要检查'))throw Error('false stale alarm');
'''
    subprocess.run(['node', '-e', code, str(source)], check=True)
