"""Assign complete episodes to the book's allocated Agent models."""
from __future__ import annotations

from pathlib import Path
import time

from novel_manga.application.agents.sandbox import load_config
from novel_manga.util import read_json
from . import conductor_common


def configure(conductor):
    groups = {}
    allocated = {r.get('endpoint'): r for r in conductor.cfg.get('planning', {}).get('models', [])}
    for endpoint, filename in conductor.agent_settings.get('model_configs', {}).items():
        if endpoint not in allocated:
            raise ValueError(f'Agent model is not allocated to this book: {endpoint}')
        path = (conductor_common.REPO/filename).resolve()
        config = load_config(path)
        server = allocated[endpoint]
        if config['model'] != server['model']:
            raise ValueError(f'Agent config and resource model disagree: {endpoint}')
        if int(server.get('slots', 0)) < 1:
            raise ValueError(f'Agent model capacity must be positive: {endpoint}')
        groups[endpoint] = {'path': str(path), 'config': config, 'slots': int(server['slots'])}
    conductor.agent_model_groups = groups
    if not groups:
        return
    default = conductor.agent_settings.get('config') or next(iter(groups.values()))['path']
    primary = load_config(Path(default))['model']
    conductor.agent_primary_model = next((k for k,g in groups.items() if g['config']['model'] == primary), None)
    if conductor.agent_primary_model is None:
        raise ValueError('Primary Agent model must be allocated in model_configs')
    for row in conductor.agents.values():
        if not row.get('run_dir'):
            continue
        run = Path(row['run_dir'])
        if any((run/name).exists() for name in ('agent-run.json','prepared.json','execution.json')):
            row.setdefault('model_endpoint', conductor.agent_primary_model)
            observe(conductor, row, read_json(run/'agent-run.json', {}))


def observe(conductor, row, result, *, config_path=None):
    groups = conductor.agent_model_groups
    if not groups:
        return
    if config_path is not None:
        endpoint = next((k for k,g in groups.items() if Path(g['path']) == config_path), None)
        if endpoint is not None:
            row.update(model_endpoint=endpoint, model=groups[endpoint]['config']['model'])
    switched = (result.get('model_switch') or {}).get('to_model')
    endpoint = next((k for k,g in groups.items() if g['config']['model'] == switched), None)
    if endpoint is not None:
        row.update(model_endpoint=endpoint, model=groups[endpoint]['config']['model'])


def assign(conductor, row):
    groups = conductor.agent_model_groups
    if not groups:
        return True
    if row.get('pid'):
        return False  # The current host and its accepted operations retain their model.
    from .agent_preparation import independent,lane,fresh_production
    from .batch_review import text_host
    split=independent(conductor)
    kind=lane(conductor,row) if split else None
    active = {key: sum(text_host(conductor,r) and r.get('model_endpoint') == key
                       and (not split or lane(conductor,r)==kind)
                       for r in conductor.agents.values()) for key in groups}
    # No Agent session or media exists yet: a stale assignment must not leave
    # a ready first production waiting while the other allocated model is free.
    assigned = None if fresh_production(conductor,row) else row.get('model_endpoint')
    if assigned and assigned not in groups:
        row.update(status='blocked', phase='blocked', reason=f'Agent model no longer allocated: {assigned}')
        return False
    if not assigned:
        available = [k for k,g in groups.items() if active[k] < g['slots']]
        if not available:
            return False
        assigned = min(available, key=lambda k: active[k]/groups[k]['slots'])
    if active[assigned] >= groups[assigned]['slots']:
        saved = read_json(Path(row['run_dir'])/'agent-run.json', {}) if row.get('run_dir') else {}
        batch=conductor.agent_settings.get('queue')=='batch_review'
        movable = ((batch or (conductor.agent_settings.get('first_pass_priority') and kind == 'normal'))
                   and saved.get('status') == 'deferred' and not saved.get('model_switch')
                   and (saved.get('job') or {}).get('status') not in {'accepted', 'running'})
        available = [key for key, group in groups.items() if active[key] < group['slots']]
        if not movable or not available:
            return False
        previous = assigned
        assigned = min(available, key=lambda key: active[key]/groups[key]['slots'])
        row['model_reassignment'] = {'from': previous, 'to': assigned, 'at': time.strftime('%F %T'),
            'reason': 'Resume idle session on free allocated model capacity'}
    row.update(model_endpoint=assigned, model=groups[assigned]['config']['model'])
    return True


def options(conductor, row=None):
    endpoint = (row or {}).get('model_endpoint')
    group = conductor.agent_model_groups.get(endpoint)
    return {**conductor.agent_settings, **({'config': group['path']} if group else {})}
