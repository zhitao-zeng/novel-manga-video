"""One evidenced input/window recovery; no implicit video or voice allowance."""
from __future__ import annotations
import copy
import fcntl
import time
from pathlib import Path
from novel_manga.util import atomic_write_json,read_json
from .input_effects import effects


def evidence(ctx,cid):
    source=(ctx.run_dir/'source.txt').read_text() if (ctx.run_dir/'source.txt').is_file() else ''
    paths=sorted((ctx.episode_dir/'work/input_repairs').glob('clip_*-*.json'),key=lambda p:p.stat().st_mtime_ns)
    for path in reversed(paths):
        record=read_json(path,{})
        if record.get('operation')=='upstream_asset' and record.get('status')=='prepared':
            result=record.get('result')or{}
            if (cid not in result.get('affected_clips',[]) or result.get('kind')not in {'image','design','binding'}
                    or result.get('source',{}).get('consistent')is not True
                    or record.get('signature',{}).get('source_text')!=source):continue
            candidate=copy.deepcopy(result.get('input_decision'))
            asset=result.get('asset')
            if asset:
                if (asset.get('image_review',{}).get('consistent')is not True
                        or not (ctx.source_novel_dir/asset['path']).is_file()):continue
                if not candidate:continue  # A candidate image must be registered before input can refer to it.
            elif result.get('kind')!='binding' or not candidate:continue
            candidate['clip_id']=cid
            return {'kind':result['kind'],'evidence_path':str(path),'reason':result['reason'],'candidate':candidate}
        if record.get('clip_id')!=cid:continue
        # These are host-owned compatibility recoveries with an exact deployed policy.
        from .input_recovery_policy import posture_protocol_recoverable,asset_configuration_recoverable,source_review_recoverable
        if (posture_protocol_recoverable(record) or asset_configuration_recoverable(ctx,record)
                or source_review_recoverable(record)):
            return {'kind':'host_fix','evidence_path':str(path),'reason':'Current host fixes this recorded protocol/configuration failure',
                    'candidate':copy.deepcopy(record['decision'])}
        if record.get('published') and record.get('source',{}).get('consistent')is True and cid in record.get('changed',[]):
            quote=str((record.get('decision')or{}).get('source_quote')or'')
            if quote and quote in source:return {'kind':'compiled_input','evidence_path':str(path),
                'reason':'Source-backed correction actually published','candidate':copy.deepcopy(record['decision'])}
    return {}


def grant(ctx,cid,proof,*,kind):
    if kind not in {'input','time'}:raise ValueError('recovery grants input or time, never video')
    verified=evidence(ctx,cid)
    if not verified or verified!=proof:raise ValueError('recovery requires current host-owned evidence')
    path=ctx.episode_dir/'repair_budget_grants.json'
    with (ctx.episode_dir/'repair_budget_grants.lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);grants=read_json(path,{})
        entries=grants.setdefault(cid,{}).setdefault('upstream_recovery',{})
        if entries.get(kind):return copy.deepcopy(entries[kind])
        item={'evidence':verified,'candidate':verified['candidate'],'consumed':False,
              'authorization':'approved-upstream-recovery-20261009','reason':verified['reason']}
        entries[kind]=item;atomic_write_json(path,grants);return copy.deepcopy(item)


def input_operation(ctx,cid,row,*,consume=False):
    path=ctx.episode_dir/'repair_budget_grants.json'
    def permitted(grants):
        item=grants.get(cid,{}).get('upstream_recovery',{}).get('input')or{}
        current=evidence(ctx,cid)
        allowed=bool(item and not item.get('consumed') and current==item.get('evidence')
                     and effects(row)==effects(item['candidate']) and row.get('source_quote')==item['candidate'].get('source_quote'))
        return item,allowed
    if not consume:
        item,allowed=permitted(read_json(path,{}));return {'allowed':allowed,'grant':item}
    with (ctx.episode_dir/'repair_budget_grants.lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);grants=read_json(path,{});item,allowed=permitted(grants)
        if allowed:item['consumed']=True;item['consumed_at']=time.time();atomic_write_json(path,grants)
        return {'allowed':allowed,'grant':copy.deepcopy(item)}


def resume_window(ctx,saved):
    if saved.get('upstream_recovery') or saved.get('passed') or (saved.get('job')or{}).get('status')in {'accepted','running'}:return None
    if float(saved.get('remaining_seconds',1)or 0)>0:return None
    grants=read_json(ctx.episode_dir/'repair_budget_grants.json',{})
    for cid,entry in grants.items():
        item=entry.get('upstream_recovery',{}).get('time')or{}
        if not item or item.get('consumed'):continue
        # A grant is written only after verifying evidence; recheck while the plan still exists.
        if (ctx.episode_dir/'clip_plan.json').exists() and evidence(ctx,cid)!=item['evidence']:continue
        seconds=600 if read_json(ctx.episode_dir/'clip_plan.json',{}).get('clips')else 1200
        with (ctx.episode_dir/'repair_budget_grants.lock').open('a')as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);current=read_json(ctx.episode_dir/'repair_budget_grants.json',{})
            marker=current[cid]['upstream_recovery']['time']
            if marker.get('consumed'):continue
            marker['consumed']=True;atomic_write_json(ctx.episode_dir/'repair_budget_grants.json',current)
        window={'seconds':seconds,'proposals':0,'evidence':item['evidence'],'prior_remaining_seconds':saved.get('remaining_seconds',0)}
        result={**saved,'upstream_recovery':window,'status':'deferred','phase':'ready_agent',
                'reason':'Continue the evidenced recovery once','remaining_seconds':seconds,'deadline':time.time()+seconds,
                'no_progress':0,'fallback_invocations':0,'updated_at':time.strftime('%F %T')}
        atomic_write_json(ctx.run_dir/'agent-run.json',result);return result
    return None


def invocation_limit_reached(result):
    """The existing recovery window also bounds technical invocation retries."""
    window = result.get('upstream_recovery') or {}
    return bool(window) and window.get('proposals', 0) >= window.get('max_invocations', 2)


def record_proposal(ctx):
    path=ctx.run_dir/'agent-run.json';saved=read_json(path,{})
    window=saved.get('upstream_recovery')
    if not window:return
    if window.get('proposals',0)>=2:raise ValueError('bounded recovery used its two proposal turns')
    window['proposals']=int(window.get('proposals',0))+1;atomic_write_json(path,saved)
