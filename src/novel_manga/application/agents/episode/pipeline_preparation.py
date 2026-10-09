"""Fixed planning/preparation before the existing episode review Agent."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from novel_manga.application.preparation.flow import prepare_one
from novel_manga.application.agents.episode.input_repair import refresh_inputs
from novel_manga.llm.client import ask_json, obj
from novel_manga.application.profiles import h3_stamp, h3_prompt_outdated
from novel_manga.story.h3 import request_issues
from novel_manga.util import atomic_write_json, read_json

from novel_manga.application.production.artifacts import media_started, archive_adoption

CORRECTION_POLICY = 3


class AssetsPending(Exception):
    def __init__(self,paths):
        self.paths=paths
        super().__init__('Shared batch assets are still being prepared')


def wait_for_shared_assets(ctx,plan):
    from novel_manga.application.production.asset_preparation import queued_asset_paths
    manifest=os.environ.get('NOVEL_ASSET_PREPARATION_MANIFEST')
    if manifest:
        paths=queued_asset_paths(read_json(Path(manifest),[]),plan,ctx.source_episode_dir.parent,ctx.novel_dir,
            state=read_json(Path(manifest).with_name('state.json'),{}))
        if paths:raise AssetsPending(paths)


def semantic_failure(failure):
    blocks=[reason for value in (failure.get('blocks') or {}).values()
            for reason in (value if isinstance(value,list) else [value])]
    return (failure.get('status')!='needs_source' and bool(
        (failure.get('audit') or {}).get('issues') or failure.get('request_conflicts')
        or any(str(reason).startswith('risk:') for reason in blocks)))


def semantic_pending(ctx):
    current=read_json(ctx.run_dir/'pipeline-production.json',{})
    return current.get('status')=='semantic_repair' and semantic_failure(current.get('failure') or {})


def mark_semantic_inputs(ctx,failure):
    prepared=read_json(ctx.run_dir/'prepared.json',{})
    prepared.setdefault('repair_round_start',len(read_json(ctx.run_dir/'tool-repair-decisions.json',[])))
    atomic_write_json(ctx.run_dir/'prepared.json',{**prepared,'ready':False,'native_preparation_pending':True})
    preparation_targets(ctx,failure)


def reviewable(ctx):
    from novel_manga.application.preparation.store import has_video
    return has_video(ctx.episode_dir)


def preparation_targets(ctx, failure):
    from novel_manga.application.agents.episode.local_preparation import _blocked
    plan=read_json(ctx.episode_dir/'clip_plan.json',{})
    ids=set(failure.get('request_conflicts') or {}) | set(failure.get('request_errors') or {}) | set(failure.get('blocks') or {})
    issues=(failure.get('audit') or {}).get('issues') or []
    from novel_manga.story.h3 import request_issues
    for clip in plan.get('clips',[]):
        if ((not issues and not ids and request_issues(clip))
                or any(i.get('stage') in clip.get('shot_indexes',[]) for i in issues)):
            ids.add(clip['clip_id'])
    wanted={c['clip_id'] for c in plan.get('clips',[])}
    ids=ids & wanted or wanted
    return _blocked(ctx,{cid:str(failure.get('reason') or failure.get('blocks') or issues)[:1000] for cid in ids},
                    source_audit=failure.get('audit'))


def ensure_cards(ctx, plan):
    """Build shared base cards under the book's existing lock, then copy them in."""
    from novel_manga.application.preparation import actions
    from novel_manga.application.agents.episode.inputs import _catalog, _references
    wait_for_shared_assets(ctx,plan)
    original=ctx.source_episode_dir.parent
    bible=read_json(original/'story_bible.json',{})
    shared={('character',c['name']):f'character_{i:03d}' for i,c in enumerate(bible.get('characters',[]),1)}
    shared.update({('location',s.split('：',1)[0].strip()):f'location_{i:03d}'
                   for i,s in enumerate(bible.get('locations',[]),1)})
    needed={r['asset_id'] for r in _references(plan)
            if shared.get((r.get('role'),r.get('name')))==r.get('asset_id')
            and not (ctx.novel_dir/r['path']).is_file() and not (original/r['path']).is_file()}
    if needed:
        actions.run_tool(['scripts/build_cards_thin.py','--novel-dir',str(original),
                          '--assets',','.join(sorted(needed)),'--tier','fast','--review'])
    _catalog(ctx,list(_references(plan)))
    actions.ensure_cards(ctx.episode_dir,plan)


def patch_inputs(ctx, drafts, repairs, failure):
    """A bounded translation repair; the model cannot rewrite source dialogue."""
    if semantic_failure(failure):
        return {'ok':False,'needs_semantic_repair':True}
    plan=read_json(ctx.episode_dir/'clip_plan.json',{})
    if not plan.get('clips'):
        return {'ok':False,'error':'no existing clips for request correction'}
    atomic_write_json(ctx.run_dir/'prepared.json',{
        **read_json(ctx.run_dir/'prepared.json',{}),'ready':False,'native_preparation_pending':True})
    targets=preparation_targets(ctx,failure)['blocked']
    selected=[c for c in plan['clips'] if c['clip_id'] in targets]
    indexes={i for c in selected for i in c.get('shot_indexes',[])}
    script=read_json(ctx.episode_dir/'chapter_script.json',{})
    from .requests import dialogue_slots
    payload={'failure':{k:failure[k] for k in ('status','reason','blocks') if k in failure},
        'shots':[s for i,s in enumerate(script.get('shots',[]),1) if s.get('index',i) in indexes],
        'clips':[{**{k:c.get(k) for k in ('clip_id','shot_indexes','stage_count','request_seconds',
            'dialogue_bindings','references','prompt_h3')},'dialogue_slots':dialogue_slots(c)} for c in selected]}
    prompt=('补全或修正这些H3英文镜头正文的格式。镜头、事件、人物、对白、时长都已确定，不作编剧修改。'
        '每个镜头返回一条英文正文，按dialogue_slots中该镜头的插槽顺序填入；每个镜头都从<DIALOGUE_1>开始，空列表表示无对白。'
        '不要返回中文台词或完整H3标记，程序会填入原对白、说话人和固定格式。不要重复动作，不写元评论。'
        'soundscape仅写英文环境声音。只输出prompts。\n'+json.dumps(payload,ensure_ascii=False))
    schema=obj({'prompts':obj({c['clip_id']:obj({
        'shots':{'type':'array','minItems':len(c['shot_indexes']),'maxItems':len(c['shot_indexes']),
                 'items':{'type':'string'}},'soundscape':{'type':'string'}}) for c in selected})})
    answer=ask_json([{'type':'text','text':prompt}],schema,
        name='pipeline_request_correction',max_tokens=6000,timeout=300)
    path='output/pipeline-repair.json'
    decisions=[{'clip_id':cid,'action':'edit','reason':'Repair the existing English request structure',
                'h3':body} for cid,body in answer['prompts'].items()]
    atomic_write_json(ctx.work_dir/path,{'decisions':decisions})
    checked=drafts.check({'file':path})
    return repairs.apply({'file':path}) if checked.get('valid') is True else checked


def correct_requests(ctx,drafts,repairs,blocked):
    """One structural correction for failed English preparation, without new authoring."""
    path=ctx.episode_dir/'clip_plan.json';plan=read_json(path)
    notes=read_json(ctx.episode_dir/'review_feedback.json',{})
    due={c['clip_id']:c for c in plan['clips']if c['clip_id']in blocked and
         (h3_prompt_outdated(c,notes.get(c['clip_id'],''),strict=True)or request_issues(c))}
    targets={cid:blocked[cid]for cid,c in due.items()if not c.get('prompt_h3')or request_issues(c)}
    stamp={cid:h3_stamp(due[cid],notes.get(cid,''))for cid in targets}
    receipt=ctx.run_dir/'native-request-correction.json';previous=read_json(receipt,{})
    if targets and previous.get('inputs')!=stamp:
        atomic_write_json(receipt,{'inputs':stamp,'status':'attempted'})
        result=patch_inputs(ctx,drafts,repairs,{'status':'needs_repair','request_errors':targets})
        atomic_write_json(receipt,{'inputs':stamp,'status':'completed','result':result})
    elif targets and not previous.get('rechecked_saved_slots'):
        errors=(previous.get('result')or{}).get('argument_errors')or{}
        saved='output/pipeline-repair.json'
        if errors and all('dialogue slots must occur once in order'in str(e)for e in errors.values()) and (ctx.work_dir/saved).is_file():
            previous['rechecked_saved_slots']=True
            atomic_write_json(receipt,previous)
            checked=drafts.check({'file':saved})
            previous['result']=repairs.apply({'file':saved})if checked.get('valid')is True else checked
            atomic_write_json(receipt,previous)
    current={c['clip_id']:c for c in read_json(path)['clips']}
    return {cid:why for cid,why in blocked.items()if cid in current and
            (h3_prompt_outdated(current[cid],notes.get(cid,''),strict=True)or request_issues(current[cid]))}


def prepare(ctx, drafts, repairs):
    """Keep filmed work intact; use bounded native stages for unfilmed inputs."""
    path=ctx.run_dir/'pipeline-production.json'
    prior=read_json(path,{})
    if (ctx.work_dir/'input/repack-needed.json').exists():
        return {'ready':False,'status':'semantic_repair','needs_semantic_repair':True,'stage':'repack'}
    if media_started(ctx.episode_dir):
        return {'ready':True,'existing_media':True}
    def save(status, **details):
        prior.update(status=status,at=time.time(),**details)
        atomic_write_json(path,prior)
        return dict(prior)
    save('preparing')
    # The native planner needs the chapter's detailed reading, not only the
    # coarser whole-book merge copied by the historical workspace snapshot.
    from novel_manga.episodes import chapter_of
    from novel_manga.application.agents.episode.inputs import _copy
    chapter=chapter_of(ctx.episode_dir.name)
    _copy(ctx.source_episode_dir.parent/f'lean/extract/ch-{chapter:03d}.json',
          ctx.novel_dir/f'lean/extract/ch-{chapter:03d}.json')
    # Adopt a valid existing author draft without asking a new writer to redo it.
    if not (ctx.episode_dir/'clip_plan.json').exists() and (ctx.work_dir/'output/episode.json').exists():
        try:
            if drafts.check({}).get('valid') is True:
                drafts.adopt({})
        except (ValueError,KeyError,TypeError):
            pass  # Retain the candidate; the normal planner still owns missing plans.
    try:
        wait_for_shared_assets(ctx,read_json(ctx.episode_dir/'clip_plan.json',{}))
        failure=prepare_one(ctx.episode_dir,card_builder=lambda plan:ensure_cards(ctx,plan))
        if semantic_failure(failure):
            mark_semantic_inputs(ctx,failure)
            return save('semantic_repair',ready=False,stage='input_repair',
                        needs_semantic_repair=True,failure=failure)
        if failure.get('status') != 'ready' and failure.get('status') != 'needs_source':
            if not prior.get('correction_attempted') or prior.get('correction_policy') != CORRECTION_POLICY:
                prior['correction_attempted']=True
                prior['correction_policy']=CORRECTION_POLICY
                save('correcting',failure=failure)
                correction=patch_inputs(ctx,drafts,repairs,failure)
                save('correcting',failure=failure,correction_result=correction)
                if correction.get('ok'):
                    failure=prepare_one(ctx.episode_dir,card_builder=lambda plan:ensure_cards(ctx,plan))
        if semantic_failure(failure):
            mark_semantic_inputs(ctx,failure)
            return save('semantic_repair',ready=False,stage='input_repair',needs_semantic_repair=True,failure=failure)
        if failure.get('status') != 'ready':
            return save('blocked',ready=False,stage='preparation',failure=failure)
        plan=read_json(ctx.episode_dir/'clip_plan.json')
        script=read_json(ctx.episode_dir/'chapter_script.json')
        refresh_inputs(ctx,plan,script)
        atomic_write_json(ctx.run_dir/'prepared.json',{
            **read_json(ctx.run_dir/'prepared.json',{}),'ready':True,'native_preparation_pending':False,
            'method':'native production pipeline',
            'repair_round_start':len(read_json(ctx.run_dir/'tool-repair-decisions.json',[]))})
        from novel_manga.application.preparation.store import inputs
        return save('ready',ready=True,stage='render',inputs=inputs(ctx.episode_dir))
    except AssetsPending as pending:
        return save('waiting_assets',ready=False,stage='assets',waiting_assets=pending.paths)
    except Exception as error:
        return save('blocked',ready=False,stage='preparation',error=f'{type(error).__name__}: {error}')


def semantic_prompt():
    return ('The fixed production pipeline has an existing screenplay with a confirmed semantic '
        'or reference-content problem. You are its targeted repair Agent, not its initial writer. '
        'Read input/latest-preparation.json, current source and only the affected shots/references. '
        'Correct meaning yourself: input shots[].turns may replace/delete/add source-backed turns, '
        'fix speaker_name and delivery_mode/inner_monologue while preserving cuts and duration. '
        'Keep essential facts, causality and who knows each fact. Ordinary H3 edit cannot change dialogue. '
        'Use check output/repair.json then apply. Source checks remain independent. If the finding is a '
        'false positive, use apply action=recheck_input with reason and verbatim source_quote. '
        'Only if existing cuts cannot carry the required story, request apply action=replan with '
        'reason, source_quote and specific instructions; the normal planner executes that explicit brief. '
        'Do not write a replacement episode.json. After input repair, render resumes native preparation '
        'and video generation. Missing files and ordinary translation stay with the fixed pipeline. '
        'Commands: python3 /work/tools/episode.py check output/repair.json; apply; render; finish. '
        'End this turn when yield=true.\n')


def begin_semantic_repair(ctx):
    marker=ctx.run_dir/'pipeline-input-repair-started.json'
    if marker.exists():return False
    session=ctx.work_dir/'episode-session.json'
    if session.exists():session.rename(ctx.run_dir/'pipeline-authoring-session.json')
    atomic_write_json(marker,{'phase':'input_repair','at':time.time()})
    return True


def validate_editorial_request(ctx, row):
    if media_started(ctx.episode_dir):
        raise ValueError('existing takes retain their cuts and budgets; use scoped input/turns repair')
    if not semantic_pending(ctx):
        raise ValueError('editorial operations require a current recorded semantic input problem')
    if row.get('action') not in {'replan','recheck_input'} or not str(row.get('reason') or '').strip():
        raise ValueError('editorial operation needs action and reason')
    quote=str(row.get('source_quote') or '').strip()
    if not quote or quote not in (ctx.run_dir/'source.txt').read_text():
        raise ValueError('editorial operation needs a verbatim source_quote')
    if row['action']=='replan' and not str(row.get('instructions') or '').strip():
        raise ValueError('replan needs the Agent concrete correction instructions')


def editorial_request(ctx, row):
    validate_editorial_request(ctx,row)
    if row['action']=='replan':
        path=ctx.run_dir/'pipeline-replan-request.json'
        if path.exists():
            raise ValueError('the single requested whole-plan revision has already been used; continue scoped repair')
        archive_adoption(ctx.episode_dir, ctx.run_dir)
        atomic_write_json(path,{'status':'running','decision':row,'at':time.time()})
        from novel_manga.application.preparation.actions import replan
        replan(ctx.episode_dir,[{'agent_reason':row['reason'],'source_quote':row['source_quote'],
                               'instructions':row['instructions']}])
        atomic_write_json(path,{'status':'completed','decision':row,'at':time.time()})
        return {'ok':True,'input_repaired':[row['clip_id']],'replanned':True,'changed':[],
                'next':'Native preparation rechecks the revised screenplay before generation.'}
    from novel_manga.application.preparation.store import inputs
    from novel_manga.application.preparation.audit import audit
    from novel_manga.planning.preparation import POLICY
    path=ctx.run_dir/'pipeline-source-recheck.json';stamp=inputs(ctx.episode_dir)
    saved=read_json(path,{})
    if saved.get('inputs')==stamp:
        answer=saved['answer']
    else:
        answer=audit(ctx.episode_dir,concerns={'agent_dispute':row['reason'],'source_quote':row['source_quote']})
        stamp=inputs(ctx.episode_dir)
        atomic_write_json(path,{'inputs':stamp,'answer':answer,'decision':row})
    atomic_write_json(ctx.episode_dir/'pre_render_story_audit.json',{
        'policy':POLICY,'inputs':stamp,'answer':answer,'at':time.strftime('%F %T')})
    passed=answer.get('source_readable') is True and not answer.get('issues')
    if not passed:
        preparation_targets(ctx,{'audit':answer})
    return {'ok':passed,'source_passed':passed,'input_repaired':[row['clip_id']] if passed else [],
            'changed':[],'blocked':{} if passed else {row['clip_id']:str(answer.get('issues') or answer.get('source_problem'))}}


def review_prompt():
    return ('Normal production has already run through the fixed pipeline. Your job is to review '
        'and repair the existing video. Do not author a new episode.json or repeat book reading/planning. '
        'Use current status, input/latest-render.json and input/latest-review.json, the relevant source '
        'and actual frame/audio/reference evidence. Reuse current independent checks; do not resubmit '
        'accepted clips merely to repeat their review. For a real defect use existing check/apply input, '
        'edit, retake or keep operations within current allowances. The host performs rendering. '
        'Read only relevant shared repair cases, and use finish for the actual accepted or unresolved '
        'outcome. Tool commands: python3 /work/tools/episode.py check output/repair.json; apply; '
        'review; render; finish. End the turn when yield=true. Preserve existing cuts and dialogue '
        'unless a source-backed supported repair explicitly permits correction. Use input shots[].turns '
        'to correct actual dialogue/inner-voice meaning errors against the original source.\n')


def begin_review(ctx):
    """Leave an old authoring conversation behind at this one phase transition."""
    marker=ctx.run_dir/'pipeline-review-started.json'
    if marker.exists():
        return False
    session=ctx.work_dir/'episode-session.json'
    previous=read_json(session,{})
    if session.exists() and not (ctx.run_dir/'pipeline-input-repair-started.json').exists():
        session.rename(ctx.run_dir/'pipeline-authoring-session.json')
    atomic_write_json(marker,{'phase':'review','at':time.time(),'previous_session':previous})
    return True



def prepare_repacked(ctx,clip_ids):
    """Translate only new cuts even when another preserved clip already has media."""
    from novel_manga.application.rendering.h3 import convert
    from novel_manga.story.h3 import request_issues
    plan=read_json(ctx.episode_dir/'clip_plan.json');script=read_json(ctx.episode_dir/'chapter_script.json')
    try:
        ensure_cards(ctx,plan)
        problems={}
        for clip in plan['clips']:
            if clip['clip_id']not in clip_ids or clip.get('kind')!='video':continue
            errors=[];convert(clip,note='',episode_dir=ctx.episode_dir,source_script=script,problems=errors)
            errors.extend(request_issues(clip))
            if errors:problems[clip['clip_id']]=errors
        atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
        refresh_inputs(ctx,plan,script)
        if problems:
            preparation_targets(ctx,{'request_errors':problems,'reason':'New cuts require targeted request correction'})
            return {'ready':False,'problems':problems}
        return {'ready':True}
    except Exception as error:return {'ready':False,'error':f'{type(error).__name__}: {error}'}
