"""Per-episode native preparation before the existing exception Agent dispatch."""
from __future__ import annotations

from pathlib import Path

from novel_manga.application.agents.episode.context import create_context
from novel_manga.util import atomic_write_json, read_json
from . import conductor_common, conductor_workers

FINAL = {'ready', 'needs_source', 'needs_replan', 'needs_repair', 'error', 'existing_video', 'production_owned'}


def independent(conductor):
    return bool(conductor.agent_settings.get('production_parallel'))


def fresh_production(conductor,row):
    if not independent(conductor) or not row.get('run_dir'):return False
    if row.get('issue_retry_started') or row.get('status') in {'finished','finished_with_issues','blocked'}:
        return False
    run=Path(row['run_dir'])
    if any((run/name).exists() for name in ('agent-run.json','prepared.json','execution.json',
            'pipeline-input-repair-started.json','pipeline-review-started.json')):return False
    from novel_manga.application.preparation.store import has_video
    source=Path(row['source_episode'])
    return not has_video(source) and not has_video(run/conductor.novel_dir.name/source.name)


def lane(conductor,row):
    return 'normal' if normal_work(conductor,row) else 'repair'


def available_hosts(conductor):
    if conductor.agent_settings.get('queue')=='batch_review':
        from .batch_review import text_host
        active=[r for r in conductor.agents.values() if r.get('pid')]
        parallel=conductor.agent_settings['parallel']
        limit=conductor.agent_settings.get('task_pool_size',parallel)
        return {'shared':max(0,min(parallel-sum(text_host(conductor,r) for r in active),
                                  limit-len({r['pid'] for r in active})))}
    if not independent(conductor):
        used={r['pid'] for r in conductor.agents.values() if r.get('pid')}
        return {'shared':max(0,conductor.agent_settings['parallel']-len(used))}
    limits={'normal':conductor.agent_settings['production_parallel'],
            'repair':conductor.agent_settings['parallel']}
    return {kind:max(0,limit-len({r['pid'] for r in conductor.agents.values()
        if r.get('pid') and lane(conductor,r)==kind})) for kind,limit in limits.items()}


def episode_plan(conductor,row):
    source=Path(row['source_episode'])
    local=Path(row['run_dir'])/conductor.novel_dir.name/source.name
    directory=local if (local/'clip_plan.json').is_file() else source
    return read_json(directory/'clip_plan.json',{}),directory.parent


def first_pass_work(conductor, row):
    if row.get('passed') or row.get('status') == 'finished':
        return False
    from novel_manga.application.preparation.store import unfilmed_video_clips
    plan, book = episode_plan(conductor, row)
    missing = unfilmed_video_clips(book/Path(row['source_episode']).name, plan)
    row['first_pass_remaining'] = len(missing) if plan.get('clips') else None
    return bool(missing) or not plan.get('clips')


def asset_plan(conductor, row):
    plan, book = episode_plan(conductor, row)
    if conductor.agent_settings.get('first_pass_priority') and first_pass_work(conductor, row):
        from novel_manga.application.preparation.store import unfilmed_video_clips
        missing = unfilmed_video_clips(book/Path(row['source_episode']).name, plan)
        plan = {**plan, 'clips': [c for c in plan.get('clips', []) if c['clip_id'] in missing]}
    return plan, book


def assets_waiting(conductor,row):
    batch=conductor.agent_settings.get('queue')=='batch_review'
    if batch:
        if row.get('batch_stage')!='input_repair':return False
    elif not independent(conductor) or not normal_work(conductor,row):return False
    from .asset_preparation import queued_asset_paths
    manifest=conductor.agent_settings.get('asset_manifest')
    if not manifest:return False
    plan,local=asset_plan(conductor,row)
    missing=queued_asset_paths(read_json(Path(manifest),[]),plan,conductor.novel_dir,local,
                              state=read_json(Path(manifest).with_name('state.json'),{}))
    if missing:
        row.update(status='waiting_assets',phase='waiting_assets',waiting_for='assets',
            waiting_assets=missing,production_lane='normal')
        return True
    if row.get('status')=='waiting_assets':
        row.update(status='pending',phase='ready_agent',waiting_for=None,waiting_assets=[])
    return False


def asset_ready(conductor,row):
    plan,local=asset_plan(conductor,row)
    return bool(plan.get('clips')) and all(
        any(p.is_file() and p.stat().st_size>0 for p in (local/r['path'],conductor.novel_dir/r['path']))
        for c in plan['clips'] for r in c.get('references',[])
        if r.get('role') in {'character','location','prop'} and r.get('path'))


def task_pool(conductor,candidates,active):
    limit=conductor.agent_settings.get('task_pool_size')
    if not limit:return candidates
    if conductor.agent_settings.get('queue')=='batch_review':
        from .batch_review import order
        # Retain every owner; idle review members cannot hide input exceptions
        # outside the old window. Waiting shared images do not fill runnable seats.
        available=[r for r in order(conductor,candidates,priority) if not assets_waiting(conductor,r)]
        selected=list(dict.fromkeys([*[r['episode'] for r in active],
                                     *[r['episode'] for r in available]]))
        conductor.task_pool_episodes=selected[:max(limit,len(active))]
        return [r for r in available if r['episode'] in conductor.task_pool_episodes]
    eligible={row['episode']:row for row in [*active,*candidates]}
    selected=list(dict.fromkeys([*[r['episode'] for r in active],
        *[n for n in conductor.task_pool_episodes if n in eligible]]))
    # A card completed outside the current window must be allowed to feed H3.
    # Evict only idle asset waiters; their chapter state and budgets stay intact.
    ready=[r for r in candidates if r['episode'] not in selected and normal_work(conductor,r)
           and asset_ready(conductor,r)]
    for row in ready:
        if len(selected)<limit:break
        victim=next((n for n in reversed(selected) if not eligible[n].get('pid')
                     and assets_waiting(conductor,eligible[n])),None)
        if victim is None:break
        selected.remove(victim);selected.append(row['episode'])
    for row in sorted(candidates,key=lambda r:(priority(conductor,r),not asset_ready(conductor,r),r['episode'])):
        if len(selected)>=limit:break
        if row['episode'] not in selected:selected.append(row['episode'])
    if independent(conductor):
        # First passes become repair work. Retaining every old member would
        # eventually fill the entire window with repair sessions.
        capacities={'normal':conductor.agent_settings['production_parallel'],
                    'repair':conductor.agent_settings['parallel']}
        kinds={n:lane(conductor,r) for n,r in eligible.items()}
        runnable={n:bool(r.get('pid')) or not assets_waiting(conductor,r) for n,r in eligible.items()}
        for kind,capacity in capacities.items():
            choices=sorted((r for r in candidates if r['episode'] not in selected
                and kinds[r['episode']]==kind and runnable[r['episode']]),
                key=lambda r:(not asset_ready(conductor,r) if kind=='normal' else False,
                              priority(conductor,r),r['episode']))
            for row in choices:
                counts={k:sum(kinds[n]==k and runnable[n] for n in selected) for k in capacities}
                if counts[kind]>=min(capacity,limit):break
                victim=next((n for n in reversed(selected) if not eligible[n].get('pid')
                    and (not runnable[n] or (kinds[n]!=kind and counts[kinds[n]]>capacities[kinds[n]])
                         or (conductor.agent_settings.get('first_pass_priority') and kind=='normal'
                             and kinds[n]=='repair'))),None)
                if victim is None:break
                selected.remove(victim);selected.append(row['episode'])
    conductor.task_pool_episodes=selected
    return [row for row in candidates if row['episode'] in selected]


def state_path(conductor, row):
    return conductor.novel_dir/f"h3_preparation/episodes/{row['episode']}.json"


def observe(conductor, row, processes):
    """Adopt a live preparation child after restart, then route its final result once."""
    from .control import argument
    proc = conductor.procs.get(f"prepare_{row['episode']}")
    pid = proc.pid if proc is not None and proc.poll() is None else None
    for process in processes:
        args = process['args']
        if not any(Path(arg).name == 'prepare_h3_book.py' for arg in args):
            continue
        directory = argument(args, '--novel-dir')
        if (directory and argument(args, '--episode') == str(row['episode'])
                and (Path(process.get('cwd', conductor_common.REPO))/directory).resolve() == conductor.novel_dir):
            pid = process['pid']
            break
    if pid:
        row.update(pid=pid, status='preparing', phase='preparing_inputs', waiting_for='preparation')
        return True
    if row.get('phase') == 'preparing_inputs':
        path = state_path(conductor, row)
        saved = read_json(path, {})
        if saved.get('status') not in FINAL:
            saved.update(status='error', reason='preparation worker exited without a final result')
            if not conductor.dry:
                atomic_write_json(path, saved)
        row.update(pid=None, status='pending', phase='ready_agent', waiting_for=None,
                   production_lane='normal' if saved.get('status') == 'ready' else 'repair')
    return False


def priority(conductor, row):
    if row['status'] == 'deferred':
        return 0
    if row.get('run_dir') and (Path(row['run_dir'])/'agent-run.json').is_file():
        return 1
    status = read_json(state_path(conductor, row), {}).get('status')
    return 2 if status == 'ready' else 3 if status in FINAL else 4


def normal_work(conductor, row):
    if independent(conductor) and conductor.agent_settings.get('first_pass_priority'):
        return first_pass_work(conductor, row)
    if fresh_production(conductor,row):return True
    if independent(conductor) and row.get('pipeline_phase')=='production':return True
    if row.get('issue_retry_started') or row.get('status') in {'finished_with_issues', 'blocked'}:
        return False
    if row.get('production_lane'):
        return row['production_lane'] == 'normal'
    if independent(conductor):return True
    if row.get('requested_input_mode') != 'auto' or not row.get('run_dir'):
        return False
    if any((Path(row['run_dir'])/name).exists() for name in ('agent-run.json','prepared.json','execution.json')):
        return False
    directory = Path(row['source_episode'])
    return ((directory/'chapter_script.json').is_file() and (directory/'clip_plan.json').is_file()
            and read_json(state_path(conductor, row), {}).get('status') not in FINAL-{'ready'})


def dispatch_order(conductor, candidates):
    """Reserve ordinary production positions, while continuing the existing repair queue."""
    if conductor.agent_settings.get('queue')=='batch_review':
        from .batch_review import order
        yield from order(conductor,candidates,priority)
        return
    remaining = [(row, normal_work(conductor, row), priority(conductor, row)) for row in candidates]
    if independent(conductor):
        for row,normal,_ in sorted(remaining,key=lambda item:(
                not item[1],not asset_ready(conductor,item[0]) if item[1] else False,item[2],item[0]['episode'])):
            yield row
        return
    while remaining:
        normal_active = sum(bool(r.get('pid')) and normal_work(conductor, r)
                            for r in conductor.agents.values())
        prefer_normal = normal_active < conductor.agent_settings.get('normal_parallel', 0)
        choice = min(range(len(remaining)), key=lambda i:
                     (-1 if prefer_normal and remaining[i][1] else remaining[i][2], i))
        row, _, _ = remaining.pop(choice)
        yield row


def admit(conductor, row, environment):
    """Return False when a preparation child owns this slot; True to dispatch Agent."""
    if row.get('requested_input_mode') != 'auto':
        return True
    run = Path(row['run_dir'])
    if any((run/name).exists() for name in ('agent-run.json', 'prepared.json', 'execution.json')):
        return True  # Never switch a started workspace or discard its media budget.
    directory = Path(row['source_episode'])
    from novel_manga.application.preparation.store import has_video
    if has_video(directory):
        return True
    saved = read_json(state_path(conductor, row), {})
    status = saved.get('status')
    audit = saved.get('audit') or {}
    if status == 'needs_source' or audit.get('source_readable') is False:
        row.update(status='blocked', phase='blocked',
                   preparation_status='needs_source', waiting_for='source',
                   reason=audit.get('source_problem') or saved.get('reason') or 'Original source is unreadable')
        return False
    has_plan = bool(read_json(directory/'clip_plan.json', {}).get('clips')
                    and read_json(directory/'chapter_script.json', {}).get('shots'))
    group = conductor.agent_model_groups.get(row.get('model_endpoint'), {})
    native = (group.get('config') or {}).get('production_mode') == 'pipeline_review'
    if native or not has_plan or status in FINAL:
        from novel_manga.planning.preparation import needs_full_replan
        mode = 'script' if has_plan and (native or not needs_full_replan(audit.get('issues', []))) else 'source'
        ctx = create_context(conductor.novel_dir, row['episode'],
            output_root=row['output_root'], input_mode=mode)
        row.update(input_mode=mode, run_dir=str(ctx.run_dir), preparation_status=status or 'missing_plan')
        row['production_lane'] = 'normal' if native or status == 'ready' else 'repair'
        return True
    script = Path(__file__).resolve().parents[4]/'scripts/prepare_h3_book.py'
    command = [conductor_common.PY, str(script), '--novel-dir', str(conductor.novel_dir),
               '--episode', str(row['episode'])]
    conductor_workers.spawn(conductor, f"prepare_{row['episode']}", command,
                            {**environment, 'NOVEL_AGENT_MANAGED':'1'})
    proc = conductor.procs.get(f"prepare_{row['episode']}")
    if not conductor.dry and proc is not None:
        row.update(pid=proc.pid, status='preparing', phase='preparing_inputs', waiting_for='preparation',
                   production_lane='normal')
    return False
