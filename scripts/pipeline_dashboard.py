"""Dashboard metrics from saved production state; never schedules production work.

Only the monitor's own delivery samples are written. Detailed repair aggregates
are cached by episode file mtimes and refreshed in the monitor background thread.
"""
from __future__ import annotations

from collections import Counter
import json
import os
import sqlite3
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from novel_manga.batch_control import flow_snapshot
from novel_manga.reporting.delivery import net_rates, net_windows
from dashboard_store_thin import scan_book

_DETAILS = {}
_EPISODES = {}
_NET = {}
_STARTED = set()
_LOCK = threading.Lock()
_AUDITS = {}
from novel_manga.repair.scheduling import FLOWS


def read(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):
        return default


def stamp(value: str) -> float | None:
    try:
        return time.mktime(time.strptime(value,'%Y-%m-%d %H:%M:%S'))
    except (ValueError,TypeError):
        return None


def sample_delivery(novel: Path, state: dict):
    at=stamp(state.get('updated_at'))
    passed=state.get('summary',{}).get('deliverable_precise')
    if at is None or passed is None:
        return
    path=novel/'monitor_metrics/net_delivery.json'
    samples=read(path,[])
    if not samples or at-samples[-1]['at']>=60:
        samples=[s for s in samples if s['at']>=at-48*3600]
        samples.append({'at':at,'passed':passed})
        path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(samples),encoding='utf-8')
        temp.replace(path)
    _NET[str(novel)]={'since':time.strftime('%F %T',time.localtime(samples[0]['at'])) if samples else None,
                      'sampled_at':state.get('updated_at'),'rates':net_rates(samples),'windows':net_windows(samples)}


def take_of(path: str | None):
    try:
        s=Path(path).stat()
        return [s.st_ino,s.st_size,s.st_mtime_ns]
    except (OSError,TypeError):
        return None


def block_label(reason: str) -> str:
    text=reason.lower()
    if 'budget' in text:return '有效生成次数用尽'
    if 'attribution' in text or 'source missing' in text:return '原文归属待确定'
    if 'english' in text or 'request' in text:return '生成请求待纠正'
    if 'no effective' in text or 'did not change' in text:return '未产生有效改动'
    if 'structural' in text or 'stages' in text:return '分镜结构待修复'
    return '其他准备问题'


def episode_details(directory: Path, *, scan=None) -> dict:
    directory=directory.resolve()
    scan = scan if scan is not None else scan_book(directory.parent)
    names=['repair_history/history.json','repair_routing.json','source_acceptances.json',
           'episode_review.json','thin_media_report.json','clip_plan.json','review_feedback.json','chapter_script.json','segments.json',
           'source_speaker_contract.json']
    signature=tuple(scan.stat(directory/n).st_mtime_ns if scan.stat(directory/n) else 0 for n in names)
    cached=_EPISODES.get(str(directory))
    def video_signature(paths):
        return tuple((path,tuple(scan.take(path) or [])) for path in paths)
    if cached and cached[0]==(signature,video_signature(cached[2])):
        return cached[1]
    h=scan.read(directory/names[0],{});routing=scan.read(directory/names[1],{})
    review=scan.read(directory/'episode_review.json',{});media=scan.read(directory/'thin_media_report.json',{})
    plan=scan.read(directory/'clip_plan.json',{});notes=scan.read(directory/'review_feedback.json',{})
    current={c['clip_id']:c.get('selected') or {} for c in media.get('clips',[])}
    clips={c['clip_id']:c for c in plan.get('clips',[])}
    generations={};tracked=set();changed_methods=Counter()
    for trial in h.get('trials',[]):
        if not trial.get('managed'):continue
        tracked.update(trial.get('clips',[]));changed_methods[trial.get('method','unknown')]+=1
        for render in trial.get('renders',[]):
            for cid,row in render.get('clips',{}).items():
                for take in row.get('generated_takes',[]):
                    generations.setdefault(cid,set()).add((take['video'],tuple(take['take'])))
    successful=set()
    for cid in tracked:
        row=review.get('clips',{}).get(cid,{})
        video=current.get(cid,{}).get('video') or row.get('video')
        if (video and row.get('video')==video and row.get('take')==scan.take(video)
                and row.get('verify') and row.get('story_ok') is True
                and not row.get('flash_pending') and not row.get('technical')):
            successful.add(cid)
    accepted=0
    from repair_history import source_accepted_take
    for cid,row in scan.read(directory/'source_acceptances.json',{}).items():
        clip=clips.get(cid,{})
        if (cid in successful and row.get('video')==(current.get(cid) or {}).get('video')
                and row.get('take')==scan.take(row.get('video')) and row.get('note','')==notes.get(cid,'')
                and all(clip.get(k)==v for k,v in row.get('clip',{}).items())
                and source_accepted_take(directory,clip,notes.get(cid,'')) is not None):
            accepted+=1
    from managed_repair_thin import candidates
    _,blocked=candidates(directory,review)
    blocks=[]
    for cid,reason in blocked.items():
        row=routing.get(cid,{})
        blocks.append({'episode':int(directory.name.rsplit('_',1)[1]),'clip':cid,'reason':reason,
                       'category':block_label(reason),'at':row.get('at')})
    result={'tracked':len(tracked),'passed':len(successful),'generated':sum(len(v) for v in generations.values()),
            'passed_generated':sum(len(generations.get(cid,set())) for cid in successful),'retained':accepted,
            'actions':dict(Counter(r.get('action') for r in routing.values() if r.get('action'))),
            'methods':dict(changed_methods),'blocks':blocks}
    paths=[r.get('video') for r in current.values() if r.get('video')]
    paths.extend(str(directory.parent/name) for name in ['story_bible.json','entity_index.json','bible_aliases.json'])
    paths.extend(str(directory.parent/ref['path']) for c in clips.values() for ref in c.get('references',[]) if ref.get('path'))
    paths=list(dict.fromkeys(paths))
    _EPISODES[str(directory)]=((signature,video_signature(paths)),result,paths)
    return result


def repair_details(novel: Path) -> dict:
    result={'tracked':0,'passed':0,'generated':0,'passed_generated':0,'retained':0}
    actions=Counter();methods=Counter();blocks=[]
    scan = scan_book(novel)
    for directory in scan.directories:
        row=episode_details(directory, scan=scan)
        for key in result:result[key]+=row[key]
        actions.update(row['actions']);methods.update(row['methods']);blocks.extend(row['blocks'])
    return {**result,'actions':dict(actions),'methods':dict(methods),'blocks':blocks,
            'avg_generations_passed':round(result['passed_generated']/result['passed'],2) if result['passed'] else None,
            'total_cost_per_passed':round(result['generated']/result['passed'],2) if result['passed'] else None,
            'updated_at':time.strftime('%F %T')}


def resource_snapshot(root: Path) -> dict:
    config=read(root/'configs/h3_pool.json',{})
    night=config.get('night_shift',{});path=Path(night.get('state') or '/nonexistent')
    age=time.time()-path.stat().st_mtime if path.is_file() else None
    tick=read(path,{}) if age is not None and age<=night.get('max_state_age_seconds',600) else {}
    gpu={};leases={};inspected=set()
    for machine in tick.get('machines',[]):
        host=machine.get('machine_id')
        for g in (machine.get('inspection') or {}).get('gpus',[]):
            inspected.add(host)
            for service in g.get('services',[]):gpu.setdefault((host,service.split(':',1)[-1]),[]).append(g.get('utilization',0))
        for key in ['recovery','agent','action']:
            for lease in (machine.get(key) or {}).get('leases',[]):leases[lease.get('id')]={**lease,'host':host}
    entries=[{**r,'source':'常驻'} for r in config.get('resident',[])]
    for lease in leases.values():
        if lease.get('phase')!='active' or time.time()>=float(lease.get('deadline') or 0)-night.get('drain_minutes',15)*60:continue
        entries.extend({**r,'name':r.get('unit','夜班'),'source':'夜班','host':lease['host'],
                        'service':str(r.get('unit',''))+'.service','slots':night.get('slots',config.get('slots',2))}
                       for r in lease.get('instances',[]))
    locks=Path('/proc/locks').read_text().splitlines() if Path('/proc/locks').is_file() else []
    held={line.split()[5].split(':')[-1] for line in locks if len(line.split())>5 and line.split()[1]=='FLOCK'}
    rows=[];seen=set()
    for entry in entries:
        url=entry.get('url','');host=urlsplit(url).hostname
        if not host or url in seen:continue
        seen.add(url);key=f'{host}_{urlsplit(url).port or 80}'
        directory=Path(config.get('slot_dir') or root/'outputs/.h3slots')/key
        health=read(directory/'health.json',{});utils=gpu.get((entry.get('host'),entry.get('service')))
        enabled=entry.get('enabled',True) and host not in config.get('exclude_hosts',[])
        available=(False if not enabled or (entry.get('host') in inspected and not utils) else
                   health.get('ok') if time.time()-health.get('at',0)<360 else None)
        rows.append({'name':entry.get('name',key),'source':entry['source'],'enabled':enabled,'available':available,
                     'slots':int(entry.get('slots',config.get('slots',2))),
                     'held':sum(str(p.stat().st_ino) in held for p in directory.glob('slot_*.lock')),
                     'gpu_percent':round(sum(utils)/len(utils),1) if utils else None,
                     'reason':health.get('why') if not available and enabled else None})
    return {'instances':rows,'sampled_at':time.time(),'available_instances':sum(r['available'] is True for r in rows),
            'unconfirmed_instances':sum(r['available'] is None for r in rows),
            'available_slots':sum(r['slots'] for r in rows if r['available'] is True),
            'night_instances':sum(r['source']=='夜班' and r['enabled'] for r in rows),'gpu_sample_age':round(age) if age is not None else None}


def process_alive(pid) -> bool | None:
    if not pid:
        return None
    try:
        return Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')',1)[1].split()[0]!='Z'
    except FileNotFoundError:
        return False
    except (OSError,ValueError):
        return None


def audit_metrics(novel: Path, directory: Path | None = None) -> dict | None:
    directory=directory or novel/'repair_manager'
    launch=read(directory/'audit_launch.json',{})
    if not launch:
        return None
    status=read(directory/'audit_status_qwen.json',{})
    queue=directory/'shared_audit.sqlite3'
    if not queue.is_file():
        return None
    signature=(queue.stat().st_mtime_ns,queue.stat().st_size)
    cached=_AUDITS.get(str(queue))
    if cached and cached[0]==signature:
        aggregate=cached[1]
    else:
        db=sqlite3.connect(f'file:{queue}?mode=ro',uri=True,timeout=5)
        try:
            db.execute('BEGIN')
            rows=db.execute('SELECT status,episode,result FROM checks').fetchall()
        finally:
            db.close()
        counts=Counter();verdicts=Counter();issues=Counter();failed_eps=set();episodes=set();by_model={}
        flags=['same_person_twice','species_or_gender_wrong','action_by_wrong_person','actor_missing','lead_face_swapped']
        for state,ep,raw in rows:
            counts[state]+=1;episodes.add(ep)
            if state!='done':
                continue
            answer=json.loads(raw) if raw else {}
            model=answer.get('generation_model')
            model_counts=by_model.setdefault(model,Counter()) if model else Counter()
            bad=answer.get('verdict')=='obvious' or any(answer.get(k) for k in flags)
            if bad:
                verdicts['flagged']+=1;failed_eps.add(ep)
                model_counts['flagged']+=1
            elif answer.get('verdict') in {'fine','subtle'}:
                verdicts['passed']+=1
                model_counts['passed']+=1
            else:
                verdicts['unclassified']+=1
                model_counts['unclassified']+=1
            issues.update(k for k in flags if answer.get(k))
        checked=verdicts['passed']+verdicts['flagged']
        aggregate={'total':len(rows),'episodes':len(episodes),'counts':dict(counts),'checked':checked,
                   'passed':verdicts['passed'],'flagged':verdicts['flagged'],'unclassified':verdicts['unclassified'],
                   'flagged_episodes':len(failed_eps),'issues':dict(issues),
                   'flagged_rate':round(verdicts['flagged']*100/checked,1) if checked else None,
                   'models':{model:{'total':total,'passed':by_model.get(model,{}).get('passed',0),
                                    'flagged':by_model.get(model,{}).get('flagged',0),
                                    'checked':by_model.get(model,{}).get('passed',0)+by_model.get(model,{}).get('flagged',0)}
                             for model,total in launch.get('model_counts',{}).items()}}
        _AUDITS[str(queue)]=(signature,aggregate)
    alive=process_alive(launch.get('pid'));at=status.get('at') or launch.get('scope_changed_at') or launch.get('started_at')
    when=stamp(at)
    return {**aggregate,'status':'stopped' if alive is False and status.get('status')=='running' else status.get('status','starting'),
            'alive':alive,'workers':launch.get('workers'),'model_filter':launch.get('model_filter'),
            'scope':launch.get('scope_label') or ('仅 H3 片段' if 'h3' in str(launch.get('model_filter','')).lower() else '本轮已有视频片段'),
            'updated_at':at,'age_seconds':round(time.time()-when) if when else None,
            'sampled_at':time.strftime('%F %T'),'started_at':launch.get('started_at'),
            'excluded_models':{k.removeprefix('excluded_'):v for k,v in launch.get('counts',{}).items() if k.startswith('excluded_')},
            'reused':sum(launch['reused_results'].values()) if launch.get('reused_results') else launch.get('reused_h3_results',launch.get('reused_pilot_records',0))}


def operation_metrics(novel: Path, repair_state=None, *, process_rows=None):
    root = Path(__file__).resolve().parent.parent
    config = read(root / 'configs/pipeline.json', {})
    spec = next((n for n in config.get('novels', []) if n['id'] == novel.name), {'id': novel.name})
    return flow_snapshot(root, {**spec, 'novel_dir': str(novel)}, repair_state=repair_state, rows=process_rows)


def pipeline_metrics(novel: Path, *, process_rows=None) -> dict | None:
    novel=novel.resolve()
    state=read(novel/'repair_manager/state.json',{})
    summary=state.get('summary',{})
    flows=operation_metrics(novel, state, process_rows=process_rows)
    try:
        audit=audit_metrics(novel)
    except (OSError,ValueError,sqlite3.Error) as error:
        audit={'error':type(error).__name__,'sampled_at':time.strftime('%F %T')}
    try:
        sd_audit=audit_metrics(novel,novel/'repair_manager/sd_audit')
    except (OSError,ValueError,sqlite3.Error) as error:
        sd_audit={'error':type(error).__name__,'sampled_at':time.strftime('%F %T')}
    if 'deliverable_precise' not in summary:
        primary=audit or sd_audit
        if primary:
            return {'mode':'audit','audit':primary,'status':primary.get('status'),
                    'updated_at':primary.get('updated_at'),'flows':flows}
        return {'mode':'operations','flows':flows} if any(r['status'] != 'not_started' for r in flows.values()) else None
    requests=None
    if state.get('legacy_dir'):
        directory=Path(state['legacy_dir']).parent/'inflight/h3pool'
        try:
            limit=int((directory/'limit').read_text().strip())
            inodes={str(p.stat().st_ino) for p in directory.glob('slot_*.lock')}
            held=sum(len(parts)>5 and parts[1]=='FLOCK' and parts[5].split(':')[-1] in inodes
                     for parts in (line.split() for line in Path('/proc/locks').read_text().splitlines()))
            requests={'held':held,'limit':limit}
        except (OSError,ValueError):
            pass
    stages=Counter();jobs=[]
    for job in state.get('jobs',[]):
        if job.get('status') not in {'running','pending','waiting_plan','held','needs_attention'}:continue
        flow=FLOWS.get(job['kind'],[]);step=job.get('step',0)
        stage=flow[step] if 0<=step<len(flow) else job.get('source',job['kind'])
        family=('render' if stage.startswith('render') else 'prepare' if stage in {'repair','recover','note1','note2'} else
                'review' if stage in {'check','review','review1','review2','audit','confirm'} else 'scan')
        stages[f"{job['status']}:{family}"]+=len(job.get('episodes',[])) or 1
        jobs.append({'id':job['id'],'episodes':job.get('episodes',[]),'status':job['status'],'stage':stage,
                     'started_at':job.get('started_at'),'kind':job.get('recovery_kind') or job['kind']})
    at=stamp(state.get('updated_at'));details=_DETAILS.get(str(novel))
    net = _NET.get(str(novel))
    if net is None:
        # Existing observations can draw the first page while the monitor is
        # still warming its more expensive repair-detail cache. Read only.
        samples = read(novel / 'monitor_metrics/net_delivery.json', [])
        if samples:
            net = {'since': time.strftime('%F %T', time.localtime(samples[0]['at'])),
                   'sampled_at': time.strftime('%F %T', time.localtime(samples[-1]['at'])),
                   'rates': net_rates(samples), 'windows': net_windows(samples)}
    resources=_DETAILS.get('_resources')
    if resources and resources.get('gpu_sample_age') is not None:
        resources={**resources,'gpu_sample_age':round(resources['gpu_sample_age']+time.time()-resources.get('sampled_at',time.time()))}
    return {'mode':'repair','flows':flows,'updated_at':state.get('updated_at'),'age_seconds':round(time.time()-at) if at else None,
            'controller_alive':flows['repair']['controller_alive'],'audit':audit,'sd_audit':sd_audit,
            'status':flows['repair']['status'],'total':summary.get('total'), 'deliverable':summary['deliverable_precise'],
            'scope_label':(state.get('scope') or {}).get('label'),
            'preparation':summary.get('preparation'),
            'speech_gate':(state.get('scope') or {}).get('speech_gate',read(novel/'profile.json',{}).get('speech_gate','enforce')),
            'modelscope_upload':read(novel/'modelscope_upload.json',None),
            'remaining':summary.get('total',0)-summary['deliverable_precise'], 'inspection':summary.get('inspection',{}),
            'technical':summary.get('technical',{}),'ready_clips':summary.get('repair_ready_clips'),
            'blocked_clips':summary.get('repair_blocked_clips'),'plan_blocked_clips':summary.get('plan_blocked_clips'),
            'held_episodes':summary.get('held_episodes',[]),'legacy_residual_episodes':summary.get('residual_episodes',[]),
            'shared_audit':summary.get('shared_audit',{}),'stages':dict(stages),'jobs':jobs,
            'capacity':state.get('dispatch_policy',{}),'repair':details,'net_delivery':net,
            'resources':resources,'requests':requests}


def start_monitor(root: Path, novel_ids: list[str]):
    root=root.resolve()
    with _LOCK:
        if str(root) in _STARTED:return
        _STARTED.add(str(root))
    def run():
        last_details=time.time()  # publish lightweight delivery/GPU samples first
        while True:
            for nid in novel_ids:
                novel=root/'outputs'/nid;state=read(novel/'repair_manager/state.json',{})
                if not state:continue
                try:
                    sample_delivery(novel,state)
                    if time.time()-last_details>=30:_DETAILS[str(novel)]=repair_details(novel)
                except Exception as error:
                    _DETAILS[str(novel)]={'error':type(error).__name__,'updated_at':time.strftime('%F %T')}
            if time.time()-last_details>=30:last_details=time.time()
            try:_DETAILS['_resources']=resource_snapshot(root)
            except Exception as error:_DETAILS['_resources']={'error':type(error).__name__}
            time.sleep(15)
    threading.Thread(target=run,daemon=True,name='pipeline-dashboard-metrics').start()
