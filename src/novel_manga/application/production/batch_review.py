"""Independent Agent consumer of completed or blocked standard-production work."""
from __future__ import annotations

from pathlib import Path
import time

from novel_manga.util import atomic_write_json, read_json
from . import workspaces


def tick(producer, stats, waiting):
    if producer.dry:
        producer.review_more=any(s.get('unreviewed') for s in stats.values())
        return
    if producer.cfg.get('review',{}).get('backend') != 'episode_agent':
        producer.review_more=False
        return
    if not hasattr(producer,'review_conductor'):
        agent=dict(producer.cfg['agent'])
        for name in ('production_parallel','normal_parallel','first_pass_priority'):
            agent.pop(name,None)
        agent.update(queue='batch_review',parallel=int(producer.cfg['review'].get('parallel',2)),
                     review_reserve=int(producer.cfg['review'].get('reserved_review_hosts',2)),
                     retry_issues=False,workspace_index=producer.workspace_index)
        config={**producer.cfg,'executor':'episode_agent','workspace_index':None,
                'tmp_dir':str(producer.tmp/'agent-review'),'agent':agent}
        producer.review_conductor=type(producer)(config,producer.dry)
    peer=producer.review_conductor
    # Input exceptions replenish that stock. Gating them on video stock (or an
    # unrelated endpoint's queue) strands available text models. Actual requests
    # still share model_slot with production; H3 has its own first-pass priority.
    peer.agent_dispatch_enabled=True
    producer.review_more=peer.tick()


def background(row):
    """The Agent has yielded; its existing host owns this asynchronous operation."""
    return (bool(row.get('pid')) and row.get('phase')=='tool_running'
            and (row.get('job') or {}).get('status')=='running')


def text_host(conductor,row):
    return bool(row.get('pid')) and not (
        conductor.agent_settings.get('queue')=='batch_review' and background(row))


def order(conductor,rows,priority):
    ranked = sorted(rows,key=lambda row:(priority(conductor,row),row['episode']))
    active = sum(r.get('batch_stage')=='review' and bool(r.get('pid')) for r in conductor.agents.values())
    reserve = max(0,int(conductor.agent_settings.get('review_reserve',0))-active)
    review = [r for r in ranked if r.get('batch_stage')=='review']
    return [*review[:reserve], *[r for r in ranked if r.get('batch_stage')!='review'], *review[reserve:]]


def may_dispatch(conductor, row, candidates):
    if conductor.agent_settings.get('queue') != 'batch_review':
        return True
    reserve = min(int(conductor.agent_settings.get('review_reserve',0)), conductor.agent_settings['parallel'])
    if not reserve:
        return True
    active = [r for r in conductor.agents.values() if r.get('pid')]
    if row.get('batch_stage') == 'review':
        return (not any(r.get('batch_stage')=='input_repair' for r in candidates)
                or sum(r.get('batch_stage')=='review' for r in active) < reserve)
    if any(r.get('batch_stage')=='review' for r in [*candidates,*active]):
        inputs = sum(r.get('batch_stage')!='review' and text_host(conductor,r) for r in active)
        return inputs < conductor.agent_settings['parallel']-reserve
    return True


def handoff(row):
    state=workspaces.chapter(row)
    if state['accepted']:
        return None
    receipt=workspaces.current(row) or {}
    if state['done']:
        producer=read_json(Path(row['episode_dir'])/'batch-production.json',{})
        return {'at':producer.get('at','existing-video'),
                'status':'complete','stage':'review'}
    if receipt.get('status')=='blocked':
        return {'at':receipt['at'],'status':'blocked','stage':'input_repair',
                'error':receipt.get('error') or receipt.get('blocked')}
    return None


def observe(conductor, row):
    if conductor.agent_settings.get('queue')!='batch_review':
        return True
    if not hasattr(conductor,'review_workspaces'):
        conductor.review_workspaces=read_json(Path(conductor.agent_settings['workspace_index']),{})
    index=conductor.review_workspaces
    source=index.get(str(row['episode']))
    report=handoff(source) if source else None
    if report:
        row['batch_stage']=report['stage']
    if row.get('pid'):
        return True  # Previous owners drain their current operation without interference.
    if not report:
        return False
    path=Path(row['run_dir'])/'agent-run.json'
    saved=read_json(path,{})
    if not saved:
        saved={'status':'pending','batch_handoff_at':report['at']}
        atomic_write_json(path,saved)
    if saved.get('status')=='paused':
        return False
    if report['stage']=='review' and (row.get('phase')=='waiting_assets' or saved.get('phase')=='waiting_assets'):
        saved.update(phase='ready_agent',waiting_for=None,waiting_assets=[])
        if saved.get('status')=='waiting_assets':
            saved['status']='pending'
        atomic_write_json(path,saved)
        row.update(status=saved.get('status','pending'),phase='ready_agent',waiting_for=None,waiting_assets=[])
    if (saved.get('status') in {'blocked','finished_with_issues','deferred'} and not saved.get('passed')
            and saved.get('batch_handoff_at')!=report['at'] and not conductor.agent_pause.is_file()):
        from novel_manga.application.agents.episode.recovery import remaining_at_stop
        remaining=remaining_at_stop(saved)
        if remaining>0:
            atomic_write_json(Path(row['run_dir'])/'batch-review-handoff.json',{'producer':report,'previous':saved})
            saved.update(status='deferred',phase='ready_agent',reason='Standard pipeline handed off current media/input failure',
                         batch_handoff_at=report['at'],remaining_seconds=remaining,deadline=time.time()+remaining,
                         updated_at=time.strftime('%F %T'),job=None,no_progress=0,fallback_invocations=0)
            atomic_write_json(path,saved)
            row.update(status='deferred',phase='ready_agent',reason=saved['reason'])
    return True
