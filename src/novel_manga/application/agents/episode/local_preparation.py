"""Repair an existing unfilmed script without re-authoring the whole episode."""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import subprocess
import sys

from novel_manga.application.preparation.audit import audit
from novel_manga.application.preparation.store import inputs
from novel_manga.application.profiles import h3_prompt_outdated
from novel_manga.application.rendering.h3 import convert
from novel_manga.story.h3 import request_issues
from novel_manga.util import atomic_write_json, read_json


def active(ctx):
    manifest=read_json(ctx.work_dir/'input/manifest.json', {})
    return (ctx.input_mode == 'script' and manifest.get('production_mode') != 'pipeline_review'
            and bool(manifest.get('preparation_exception')))


def stage(ctx):
    """Only mark the existing plan available to input tools; this is not media admission."""
    if not active(ctx) or (ctx.run_dir/'prepared.json').exists():
        return
    plan = read_json(ctx.episode_dir/'clip_plan.json', {})
    script = read_json(ctx.episode_dir/'chapter_script.json', {})
    if not plan.get('clips') or not script.get('shots'):
        raise ValueError('local preparation requires existing screenplay and packed clips')
    atomic_write_json(ctx.run_dir/'prepared.json', {
        'ready': False, 'local_preparation_pending': True, 'input_mode': 'script',
        'method': 'Existing provisional inputs staged for local repair; no video authorized',
        'repair_round_start': len(read_json(ctx.run_dir/'tool-repair-decisions.json', []))})


def ensure_assets(ctx):
    from .inputs import _catalog, _references
    plan = read_json(ctx.episode_dir/'clip_plan.json')
    refs = list(_references(plan))
    _, missing, _ = _catalog(ctx, refs)
    if missing:
        assets = sorted({row['asset_id'] for row in missing if row.get('asset_id')
                         and row.get('role') in {'character','location','prop'}})
        if assets:
            code = Path(__file__).resolve().parents[5]
            log = ctx.run_dir/'preparation-assets.log'
            with log.open('a') as stream:
                result = subprocess.run([sys.executable, str(code/'scripts/build_cards_thin.py'),
                    '--novel-dir',str(ctx.novel_dir),'--assets',','.join(assets),'--review'],
                    cwd=ctx.project_root,stdout=stream,stderr=subprocess.STDOUT,timeout=1200)
            if result.returncode:
                raise ValueError(f'asset preparation failed; inspect {log.name}')
        _, missing, _ = _catalog(ctx, refs)
        if missing:
            raise ValueError('references still missing: '+', '.join(r['path'] for r in missing))
    from .input_repair import refresh_inputs
    refresh_inputs(ctx, plan, read_json(ctx.episode_dir/'chapter_script.json'))


def ensure_requests(ctx):
    """One bounded native translation per missing/invalid request; preserve valid siblings."""
    path = ctx.episode_dir/'clip_plan.json'
    plan = read_json(path)
    notes = read_json(ctx.episode_dir/'review_feedback.json', {})
    due = [c for c in plan['clips'] if c.get('kind','video') == 'video'
           and (h3_prompt_outdated(c, notes.get(c['clip_id'],''), strict=True) or request_issues(c))]
    if not due:
        return {}
    receipt = ctx.run_dir/'local-request-attempts.json'
    attempted = read_json(receipt, {})
    from novel_manga.application.profiles import h3_stamp
    jobs=[];blocked={}
    for clip in due:
        cid=clip['clip_id'];stamp=h3_stamp(clip, notes.get(cid,''))
        if attempted.get(cid)==stamp:
            blocked[cid]='Automatic translation already attempted; correct this request with apply action=edit.'
        else:
            attempted[cid]=stamp
            jobs.append(clip)
    atomic_write_json(receipt,attempted)
    def one(clip):
        candidate=copy.deepcopy(clip);problems=[]
        ok=convert(candidate,tries=1,note=notes.get(clip['clip_id'],''),episode_dir=ctx.episode_dir,problems=problems)
        if not ok or request_issues(candidate):
            raise ValueError('; '.join(map(str,problems+request_issues(candidate))) or 'English request incomplete')
        return candidate
    if jobs:
        with ThreadPoolExecutor(max_workers=min(2,len(jobs))) as pool:
            pending={pool.submit(one,c):c['clip_id'] for c in jobs}
            for future in as_completed(pending):
                cid=pending[future]
                try:
                    translated=future.result()
                    plan['clips']=[translated if c['clip_id']==cid else c for c in plan['clips']]
                    atomic_write_json(path,plan)
                except Exception as error:
                    blocked[cid]=str(error)
    return blocked


def _blocked(ctx, problems, *, source_audit=None):
    prior=read_json(ctx.episode_dir/'thin_media_report.json',{})
    report={**prior,'status':'inputs_failed','clips':prior.get('clips',[]),
            'failed_clips':prior.get('failed_clips',[]),'gate_failed_clips':prior.get('gate_failed_clips',[]),
            'blocked_clips':{**(prior.get('blocked_clips')or{}),**problems}}
    atomic_write_json(ctx.episode_dir/'thin_media_report.json',report)
    answer={'ok':False,'error':'local_preparation_required','blocked':problems,
            'source_audit':source_audit,'next':'Repair these existing inputs with apply; retain cuts and unaffected requests.'}
    atomic_write_json(ctx.work_dir/'input/latest-preparation.json',answer)
    return answer


def before_render(ctx):
    plan=read_json(ctx.episode_dir/'clip_plan.json')
    stamp=inputs(ctx.episode_dir)
    cache_path=ctx.run_dir/'local-source-check.json'
    cached=read_json(cache_path,{})
    if cached.get('inputs')==stamp:
        answer=cached['answer']
    else:
        answer=audit(ctx.episode_dir)
        atomic_write_json(cache_path,{'inputs':stamp,'answer':answer})
    issues=answer.get('issues') or []
    if answer.get('source_readable') is not True or issues:
        blocked={}
        for clip in plan['clips']:
            relevant=[i for i in issues if i.get('stage') in clip.get('shot_indexes',[]) or i.get('stage')==0]
            if relevant or answer.get('source_readable') is not True:
                blocked[clip['clip_id']]='; '.join(i.get('problem','source conflict') for i in relevant) or 'Original source is unreadable'
        if not blocked:
            blocked={c['clip_id']:'Source audit contains an unresolved input issue' for c in plan['clips']}
        return _blocked(ctx,blocked,source_audit=answer)
    try:
        ensure_assets(ctx)
    except Exception as error:
        return _blocked(ctx,{c['clip_id']:str(error) for c in plan['clips']})
    blocked=ensure_requests(ctx)
    if blocked:
        return _blocked(ctx,blocked)
    prepared=read_json(ctx.run_dir/'prepared.json',{})
    atomic_write_json(ctx.run_dir/'prepared.json',{**prepared,'ready':True,'local_preparation_pending':False})
    return None
