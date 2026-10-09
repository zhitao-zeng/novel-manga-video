"""One source-connected recut that preserves unaffected requests and take evidence."""
import copy
from dataclasses import replace
from pathlib import Path
from novel_manga.application.repair import history
from novel_manga.util import read_json
from .arguments import RepairArgumentError


def validate(ctx,row):
    if set(row)-{'clip_id','action','clip_ids','reason','source_quote'}:
        raise RepairArgumentError('repack accepts a current clip range, reason and source_quote')
    source=(ctx.run_dir/'source.txt').read_text();quote=str(row.get('source_quote')or'')
    if not quote or quote not in source or not row.get('reason'):raise RepairArgumentError('repack needs a verbatim source_quote and reason')
    plan=read_json(ctx.episode_dir/'clip_plan.json');ids={c['clip_id']for c in plan['clips']}
    scope=row.get('clip_ids',[row['clip_id']])
    if not scope or row['clip_id']not in scope or set(scope)-ids:raise RepairArgumentError('repack must name current clip IDs')
    long={c['clip_id']for c in plan['clips']if c.get('request_seconds',0)>ctx.clip_seconds_max}
    if not set(scope)&long:raise RepairArgumentError('no current overlong range; use source-backed input repair')
    for cid in scope:
        if history.generation_scope(ctx.episode_dir,cid)!={cid}:raise RepairArgumentError('this source range was already repacked; preserve its budgets')
        for task in (ctx.episode_dir/'work/clips'/cid).glob('attempt_*/clip.mp4.task.json'):
            if read_json(task,{}).get('task_id') and not task.with_name('clip.mp4').is_file():
                raise RepairArgumentError('complete the existing submitted task before changing its source cuts')
    from novel_manga.application.packing.context import context_for_plan
    bounded=copy.deepcopy(plan);bounded['limits']={**bounded.get('limits',{}),'max_clip_seconds':ctx.clip_seconds_max,
        'soft_cut_seconds':ctx.clip_seconds_max*.6,'max_stages':3}
    context=context_for_plan(ctx.episode_dir,ctx.novel_dir/'story_bible.json',bounded)
    return {**row,'clip_ids':scope,'packing_context':context}


def prepare(ctx,row):
    from novel_manga.application.packing.blocked import repack
    from novel_manga.repair.proposal import RepairProposal
    checked=validate(ctx,row);plan=read_json(ctx.episode_dir/'clip_plan.json');script=read_json(ctx.episode_dir/'chapter_script.json')
    after,report=repack(ctx.episode_dir,plan,script,targets=set(checked['clip_ids']),context=checked['packing_context'])
    after['limits']={**after.get('limits',{}),'max_clip_seconds':ctx.clip_seconds_max,'soft_cut_seconds':ctx.clip_seconds_max*.6,'max_stages':3}
    old={c['clip_id']:c for c in plan['clips']};new={c['clip_id']:c for c in after['clips']}
    counts=history.generation_counts(ctx.episode_dir)
    for group in report['groups']:
        roots={cid:{'limit':history.generation_limit(ctx.episode_dir,cid),'used':counts.get(cid,0)} for cid in group['old']}
        children={}
        for child in group['new']:
            parents=[cid for cid in group['old']if set(old[cid]['shot_indexes'])&set(new[child]['shot_indexes'])]
            if not parents:raise RepairArgumentError('repack child has no recorded source parent: '+child)
            shared={i for parent in parents for i in old[parent]['shot_indexes'] if sum(i in old[p]['shot_indexes']for p in parents)>1}
            if shared and len(parents)>1:raise RepairArgumentError('ambiguous split source ownership; retain old plan: '+str(sorted(shared)))
            children[child]={'roots':parents,'baseline_count':counts.get(child,0)}
        group['source_budget']={'roots':roots,'children':children}
    changed=set(report['changed'])|{cid for group in report['groups']for cid in group['new']}
    notes={cid:v for cid,v in read_json(ctx.episode_dir/'review_feedback.json',{}).items()if cid not in changed}
    return RepairProposal(result={'changed':sorted(changed)},script=script,plan=after,notes=notes,
                          changes={'groups':report['groups']},structural_repair=report)


def apply(ctx,row):
    from novel_manga.application.repair.publication import publish_candidate
    proposal=prepare(ctx,row)
    publish_candidate(ctx.episode_dir,proposal,'agent_scoped_repack',proposal.changed)
    recorded=history.load(ctx.episode_dir);recorded['trials'][-1]['managed']=True;history.save(ctx.episode_dir,recorded)
    history.mark_extra_prepared(ctx.episode_dir,set(proposal.changed))
    from .input_repair import refresh_inputs
    refresh_inputs(ctx,proposal.plan,proposal.script)
    if all(c.get('request_seconds',0)<=ctx.clip_seconds_max for c in proposal.plan['clips'] if c.get('kind')=='video'):
        (ctx.work_dir/'input/repack-needed.json').unlink(missing_ok=True)
    return {'ok':True,'changed':proposal.changed,'repacked':proposal.structural_repair,
            'next':'Native preparation translates and checks new cuts; old source quota remains shared.'}
