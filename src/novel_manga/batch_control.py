"""One read-only view and operator entry for the existing batch controllers.

The controllers retain their queues, episode locks, budgets and state files.
This module neither schedules episodes nor computes delivery eligibility.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

FLOWS = ('production', 'prepare', 'repair')
ENTRIES = {'production': 'conductor_thin.py', 'prepare': 'prepare_h3_book.py',
           'repair': 'manage_repair_thin.py'}


def read(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def alive(pid):
    if not pid:
        return False
    try:
        return Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except (OSError, ValueError):
        return False


def processes():
    rows = []
    for path in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            pid = int(path.parent.name)
            args = [a for a in path.read_bytes().decode(errors='replace').split('\0') if a]
            if args and alive(pid):
                rows.append({'pid': pid, 'args': args, 'cwd': str((path.parent / 'cwd').resolve())})
        except OSError:
            continue
    return rows


def argument(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default


def novel_directory(root, spec):
    return (root / spec.get('novel_dir', f"outputs/{spec['id']}")).resolve()


def production_directory(root, spec):
    return (root / spec.get('tmp_dir', f"/mnt/disk1/zengzhitao/tmp/conductor-{spec['id']}")).resolve()


def controllers(root, spec, flow, rows):
    wanted = (root / 'scripts' / ENTRIES[flow]).resolve()
    novel = novel_directory(root, spec)
    result = []
    for row in rows:
        args = row['args']; cwd = Path(row['cwd'])
        if not any(Path(a).name == wanted.name and (cwd / a).resolve() == wanted for a in args):
            continue
        named = argument(args, '--novel')
        directory = argument(args, '--novel-dir')
        config = argument(args, '--config')
        if config and not directory:
            directory = read(cwd / config).get('novel_dir')
        if named == spec['id'] or (directory and (cwd / directory).resolve() == novel):
            result.append(row)
    return result


def flow_snapshot(root: Path, spec: dict, *, rows=None, repair_state=None):
    """Saved work plus actual controller/worker liveness; never refresh production files."""
    root = root.resolve(); novel = novel_directory(root, spec)
    rows = processes() if rows is None else rows
    repair = read(novel / 'repair_manager/state.json') if repair_state is None else repair_state
    preparation = read(novel / 'h3_preparation/status.json')
    production = read(production_directory(root, spec) / 'state.json')
    states = {'production': production, 'prepare': preparation, 'repair': repair}
    result = {}
    for flow, state in states.items():
        owners = controllers(root, spec, flow, rows)
        blocked = []; pending = None; active = set(); blocked_count = 0
        if flow == 'repair':
            waiting, held = set(), set()
            for job in state.get('jobs', []):
                status = job.get('status')
                if status == 'running' and alive(job.get('pid')):
                    active.add(job['pid'])
                if status in {'pending', 'waiting_plan'}:
                    waiting.update(job.get('episodes', []))
                if status in {'held', 'needs_attention', 'waiting_plan'}:
                    held.update(job.get('episodes', []))
                    reason = job.get('reason') or job.get('error') or job.get('last_error') or status
                    blocked.append(str(reason))
            pending = len(waiting); blocked_count = len(held)
        elif flow == 'prepare':
            active = {int(pid) for pid in state.get('running', {}).values() if alive(pid)}
            counts = state.get('counts', {})
            pending = counts.get('pending') if state else None
            categories = {'needs_repair': '剧本待修改', 'needs_replan': '剧本待重新规划',
                          'needs_source': '原文不可用', 'error': '执行异常'}
            for key, label in categories.items():
                if counts.get(key):
                    blocked_count += counts[key]; blocked.append(f'{label}：{counts[key]} 集')
        else:
            active = {pid for pid in state.get('workers', {}).values() if alive(pid)}
            counts = state.get('work', {})
            pending = counts.get('pending'); blocked_count = counts.get('blocked', 0)
            if blocked_count:
                blocked.append(f'待准备或被门槛阻挡：{blocked_count} 集')
        native = state.get('status', 'running' if owners else 'stopped' if state else 'not_started')
        paused = flow == 'repair' and (novel / 'repair_manager/pause').exists()
        if paused:
            status = 'pausing' if active else 'paused'
        elif native in {'complete', 'finished'} and not owners:
            status = 'complete'
        elif not owners and native in {'running', 'pausing', 'waiting_plan', 'waiting_preparation'}:
            status = 'draining' if active else 'stopped'
        else:
            status = native
        at = state.get('updated_at') or state.get('at')
        if not at and state.get('time'):
            at = time.strftime('%F %T', time.localtime(state['time']))
        result[flow] = {'status': status, 'controller_alive': bool(owners),
                        'controller_pids': [p['pid'] for p in owners], 'in_flight': len(active),
                        'pending': pending, 'blocked': blocked_count,
                        'blocked_reasons': list(dict.fromkeys(blocked)), 'updated_at': at}
    return result


def snapshot(root: Path, config: dict, *, novel=None, flow=None):
    rows = processes()
    result = []
    for spec in config.get('novels', []):
        if novel and spec['id'] != novel:
            continue
        flows = flow_snapshot(root, spec, rows=rows)
        result.append({'id': spec['id'], 'title': spec.get('title', spec['id']),
                       'active': spec.get('active', False),
                       'flows': {flow: flows[flow]} if flow else flows})
    return {'sampled_at': time.strftime('%F %T'), 'novels': result}


def spawn(root: Path, command: list[str], log: Path):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('ab') as handle:
        proc = subprocess.Popen(command, cwd=root, stdout=handle, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True,
                                env={**os.environ, 'PYTHONPATH': f'{root / "src"}:{root / "scripts"}'})
    return proc.pid


def control(root: Path, spec: dict, flow: str, action: str, *, chapters=None, workers=None):
    """Dispatch an operator action to the native controller, without replacing its state."""
    root = root.resolve(); novel = novel_directory(root, spec)
    owners = controllers(root, spec, flow, processes())
    pause = novel / 'repair_manager/pause'
    if action == 'stop':
        if flow == 'repair':
            pause.parent.mkdir(parents=True, exist_ok=True)
            if not pause.exists():
                pause.write_text(time.strftime('%F %T'))
        else:
            for row in owners:
                os.kill(row['pid'], signal.SIGTERM)
        return {'action': 'stop_requested', 'pids': [p['pid'] for p in owners]}
    if owners:
        if flow == 'repair':
            pause.unlink(missing_ok=True)
        return {'action': 'resumed' if flow == 'repair' else 'already_running',
                'pids': [p['pid'] for p in owners]}
    python = root / '.venv/bin/python'
    command = [str(python) if python.is_file() else sys.executable, str(root / 'scripts' / ENTRIES[flow])]
    if flow == 'production':
        command += ['--pipeline', str(root / 'configs/pipeline.json'), '--novel', spec['id']]
        log = production_directory(root, spec).with_suffix('.stdout.log')
    elif flow == 'prepare':
        saved = read(novel / 'h3_preparation/status.json')
        selected = chapters or ','.join(map(str, saved.get('chapters', []))) or ','.join(
            str(r['chapters']) for r in spec.get('ranges', []))
        if not selected:
            raise ValueError('准备流程需要 --chapters、已有准备范围或小说 ranges 配置')
        command += ['--novel-dir', str(novel), '--chapters', selected,
                    '--workers', str(workers or saved.get('workers') or 6)]
        log = novel / 'h3_preparation/controller.log'
    else:
        saved = read(novel / 'repair_manager/state.json')
        command += ['run', '--novel-dir', str(novel),
                    '--model-workers', str(workers or saved.get('model_workers') or 12)]
        if saved.get('legacy_dir'):
            command += ['--legacy-dir', saved['legacy_dir']]
        if saved.get('preparation_gate'):
            command.append('--prepared-only')
        log = novel / 'repair_manager/controller.log'
    # A dead coordinator can still have draining workers. Let them finish
    # before another controller can claim the same episode preparation.
    current = flow_snapshot(root, spec)[flow]
    if current['in_flight']:
        raise ValueError(f"{flow} 仍有 {current['in_flight']} 个在途任务，请等待完成后续跑")
    pid = spawn(root, command, log)
    if flow == 'repair':
        pause.unlink(missing_ok=True)
    return {'action': 'started', 'pids': [pid]}
