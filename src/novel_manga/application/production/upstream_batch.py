"""Finite evidenced upstream recovery using the existing episode executor."""
from __future__ import annotations
import argparse
import json
import os
import sys
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from novel_manga.util import read_json,atomic_write_json,load_dotenv


def eligible_rows(rows,inspect):
    selected,held=[],[]
    for row in rows:
        current=inspect(row)
        if current['accepted'] or current['live_owner']:continue
        if current['recovery_failed'] or not current['upstream_action']:
            held.append({'episode':row['episode'],'reason':current['reason']})
        else:selected.append({**row,'upstream_action':current['upstream_action']})
    return selected,held


def context(row,novel_dir):
    from novel_manga.application.agents.episode.context import create_context
    run=Path(row['run_dir'])
    ctx=create_context(novel_dir,row['episode'],output_root=run.parents[1],input_mode=row['input_mode'])
    if ctx.run_dir.resolve()!=run.resolve():raise ValueError('recovery must retain the original run directory')
    return ctx


def inspect_row(row,novel_dir):
    from novel_manga.application.production.runs import episode_status
    from novel_manga.application.production.common import pid_alive
    from novel_manga.application.agents.episode.recovery import upstream_route
    ctx=context(row,novel_dir);saved=read_json(ctx.run_dir/'agent-run.json',{})
    media=read_json(ctx.episode_dir/'thin_media_report.json',{});manifest=read_json(ctx.run_dir/'final-candidate/manifest.json',{})
    lock=ctx.source_episode_dir/'.render.lock';owner=lock.read_text().strip()if lock.exists()else ''
    live=bool(owner.isdigit() and int(owner)>0 and pid_alive(int(owner)))
    accepted=manifest.get('passed')is True and (media.get('quality_review')or{}).get('passed')is True and episode_status(ctx.episode_dir,True)=='done'
    failed=bool(saved.get('upstream_recovery')and saved.get('status')in {'blocked','finished_with_issues'})
    # Full status only distinguishes the two ordinary non-upstream fallbacks;
    # neither is eligible here. The route reads its concrete candidates itself.
    route=upstream_route(ctx,{})
    action=route if route.get('decision') and route.get('kind')in {'asset','repack','input_recovery'} else None
    if action:
        from novel_manga.application.agents.episode.upstream_assets import failed_attempt
        failed=failed or failed_attempt(ctx,action['decision'])
        previous=read_json(ctx.run_dir/'upstream-batch-attempt.json',{})
        operation=lambda d:{k:v for k,v in d.items() if k!='reason'}
        failed=failed or bool(previous.get('completed') and not previous.get('passed')
            and operation(previous.get('decision')or{})==operation(action['decision']))
    return {'accepted':accepted,'live_owner':live,'recovery_failed':failed,'upstream_action':action,
            'reason':saved.get('reason')or route.get('reason')or'No current upstream repair evidence'}


def execute_row(row,novel_dir,out):
    from novel_manga.application.agents.episode import lifecycle,runtime,bounded_recovery
    from novel_manga.application.agents.episode.inputs import prepare_inputs
    from novel_manga.application.agents.episode.tools import EpisodeTools
    from novel_manga.application.agents.episode.flow import run_episode
    from novel_manga.application.agents.episode.recovery import remaining_at_stop
    from novel_manga.application.agents.sandbox import load_config
    from novel_manga.application.repair import history
    root=Path(os.environ['NOVEL_PROJECT_ROOT']);load_dotenv(root/'.env')
    os.environ.update(row.get('environment',{}))
    os.environ.update(NOVEL_FIRST_PASS_ONLY='0',NOVEL_PRODUCTION_ONLY='0',PYTHONPATH=str(Path(__file__).resolve().parents[3]))
    ctx=context(row,novel_dir);config=load_config(Path(row['config']))
    before=history.generation_counts(ctx.episode_dir);result={'episode':row['episode'],'run_dir':str(ctx.run_dir),'before_counts':before}
    with lifecycle.episode_lock(ctx):
        prepare_inputs(ctx)
        from novel_manga.application.resolved_settings import resolve_agent_environment
        env=resolve_agent_environment(ctx,config)
        with runtime.process_environment(env):
            route=row['upstream_action'];decision=route['decision']
            path='output/upstream-batch.json';atomic_write_json(ctx.work_dir/path,{'decisions':[decision]})
            tools=EpisodeTools(ctx);check=tools.check({'file':path});result['check']=check
            if check.get('valid'):
                applied=tools.dispatch('apply',{'file':path});result['apply']=applied
                if applied.get('input_decision'):
                    proof=bounded_recovery.evidence(ctx,decision['clip_id'])
                    if proof:
                        try:bounded_recovery.grant(ctx,decision['clip_id'],proof,kind='input')
                        except ValueError as error:result['input_grant_error']=str(error)
                    atomic_write_json(ctx.work_dir/path,{'decisions':[applied['input_decision']]})
                    checked=tools.check({'file':path});result['input_check']=checked
                    if checked.get('valid'):result['input_apply']=tools.dispatch('apply',{'file':path})
                if applied.get('repacked'):
                    from novel_manga.application.agents.episode.pipeline_preparation import prepare_repacked
                    result['preparation']=prepare_repacked(ctx,applied.get('changed',[]))
                saved=read_json(ctx.run_dir/'agent-run.json',{})
                if remaining_at_stop(saved)<=0:
                    proof=bounded_recovery.evidence(ctx,decision['clip_id'])
                    if proof:
                        bounded_recovery.grant(ctx,decision['clip_id'],proof,kind='time')
                        saved=bounded_recovery.resume_window(ctx,saved)or saved
                handoff=result.get('input_apply') or applied
                if handoff.get('ok') and (handoff.get('changed') or handoff.get('input_repaired')):
                    # A batch operation happened before the bridge started. Carry
                    # its actual receipt into the normal applied_repair→render path.
                    saved=read_json(ctx.run_dir/'agent-run.json',{})
                    if (saved.get('job')or{}).get('status')not in {'accepted','running'}:
                        saved['job']={'id':f'upstream-batch-{time.time_ns()}','action':'apply',
                            'status':'completed','payload':{'file':path},'result':handoff}
                        atomic_write_json(ctx.run_dir/'agent-run.json',saved)
            else:saved=read_json(ctx.run_dir/'agent-run.json',{})
    remaining=remaining_at_stop(saved)
    if remaining>0:
        # Continue ordinary conversation slices inside this one finite attempt.
        # Never replay the upstream operation or renew its activity allowance.
        for slice_number in range(3):
            final=run_episode(ctx,config=config,timeout=max(1,int(remaining)),
                retry_issues=slice_number==0 and saved.get('status')!='deferred')
            saved=read_json(ctx.run_dir/'agent-run.json',final)
            remaining=remaining_at_stop(saved)
            if not (final.get('status')=='deferred' and final.get('phase')=='deferred'
                    and str(final.get('reason')or'').startswith('Agent slice completed') and remaining>0):break
        result['slices']=slice_number+1
        result.update(status=final.get('status'),passed=final.get('passed'),reason=final.get('reason'))
    else:result.update(status='held',passed=False,reason='No evidenced remaining activity window; original budget was not renewed')
    after=history.generation_counts(ctx.episode_dir);result.update(after_counts=after,
        counts_preserved=all(after.get(cid,0)>=used for cid,used in before.items()))
    atomic_write_json(out/f"{row['episode']}.json",result)
    atomic_write_json(ctx.run_dir/'upstream-batch-attempt.json',{'decision':decision,'completed':True,
        'passed':result.get('passed')is True,'reason':result.get('reason')})
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory',type=Path,required=True);parser.add_argument('--novel-dir',type=Path,required=True)
    parser.add_argument('--result-dir',type=Path,required=True);parser.add_argument('--project-root',type=Path,required=True)
    parser.add_argument('--execute',action='store_true');parser.add_argument('--parallel',type=int,default=8)
    parser.add_argument('--one',type=int);args=parser.parse_args(argv)
    os.environ['NOVEL_PROJECT_ROOT']=str(args.project_root)
    rows=read_json(args.inventory)['episodes'];args.result_dir.mkdir(parents=True,exist_ok=True)
    if args.one is not None:
        result=execute_row(next(r for r in rows if r['episode']==args.one),args.novel_dir,args.result_dir)
        print(json.dumps({k:result.get(k)for k in ('episode','status','passed','reason','counts_preserved')},ensure_ascii=False));return 0
    selected,held=eligible_rows(rows,lambda row:inspect_row(row,args.novel_dir))
    atomic_write_json(args.result_dir/'triage.json',{'selected':selected,'held':held})
    (args.result_dir/'triage.json').chmod(0o600)
    print(json.dumps({'selected':len(selected),'held':len(held),'execute':args.execute}))
    if not args.execute:return 0
    inventory=args.result_dir/'inventory.json';atomic_write_json(inventory,{'episodes':selected});inventory.chmod(0o600)
    def run(row):
        log=(args.result_dir/f"{row['episode']}.log").open('a')
        command=[sys.executable,'-m','novel_manga.application.production.upstream_batch','--inventory',str(inventory),
            '--novel-dir',str(args.novel_dir),'--result-dir',str(args.result_dir),'--project-root',str(args.project_root),
            '--execute','--one',str(row['episode'])]
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[3])}
        done=subprocess.run(command,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT);log.close()
        return {'episode':row['episode'],'exit_code':done.returncode}
    with ThreadPoolExecutor(max_workers=max(1,args.parallel))as pool:receipts=list(pool.map(run,selected))
    atomic_write_json(args.result_dir/'batch.json',{'workers':receipts,'held':held});return 0


if __name__=='__main__':raise SystemExit(main())
