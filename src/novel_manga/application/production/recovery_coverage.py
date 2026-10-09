"""Route every unfinished episode to its existing pipeline or repair operation."""
from pathlib import Path
from novel_manga.util import read_json
from .recovery_context import context


def inspect_context(ctx):
    from .runs import episode_status
    from .common import pid_alive
    from novel_manga.application.agents.episode.recovery import remaining_at_stop,upstream_route
    from novel_manga.application.agents.episode.repair_targets import eligible_targets
    from novel_manga.application.agents.episode.bounded_recovery import evidence,input_operation
    from novel_manga.application.agents.episode.input_records import _standard_input_route
    from novel_manga.application.agents.episode.evidence import EpisodeEvidence
    from novel_manga.application.preparation.store import unfilmed_video_clips
    from novel_manga.application.profiles import h3_prompt_outdated
    from novel_manga.story.h3 import request_issues
    saved=read_json(ctx.run_dir/'agent-run.json',{})
    plan=read_json(ctx.episode_dir/'clip_plan.json',{});clips=plan.get('clips',[])
    media=read_json(ctx.episode_dir/'thin_media_report.json',{})
    formal=read_json(ctx.run_dir/'final-candidate/manifest.json',{})
    lock=ctx.source_episode_dir/'.render.lock';owner=lock.read_text().strip()if lock.is_file()else ''
    remaining=remaining_at_stop(saved)
    records=[{'file':str(p),'clip_id':r.get('clip_id'),'status':r.get('status'),
              'step':r.get('step'),'error':r.get('error'),'details':r.get('error_details')}
             for p in sorted((ctx.episode_dir/'work/input_repairs').glob('*.json'))
             if (r:=read_json(p,{})).get('status')in {'rejected','incomplete','tool_error'}]
    result={'remaining_seconds':remaining,'blockers':media.get('blocked_clips')or{},
            'input_failures':records,'source_file':str(ctx.run_dir/'source.txt'),
            'media_file':str(ctx.episode_dir/'thin_media_report.json'),'reason':saved.get('reason')}
    def route(kind,**details):return {**result,'kind':kind,**details}
    if owner.isdigit()and int(owner)>0 and pid_alive(int(owner)):return route('live_owner')
    if (formal.get('passed')is True and media.get('quality_review',{}).get('passed')is True
            and episode_status(ctx.episode_dir,True)=='done'):return route('accepted')
    # Prefer already generated/source-checked candidates over drawing them again.
    for clip in clips:
        cid=clip['clip_id'];proof=evidence(ctx,cid)
        if not proof or not proof.get('candidate'):continue
        try:mode,_,_=_standard_input_route(ctx,cid,proof['candidate'])
        except ValueError:mode=None
        if mode=='reuse':continue
        grant=read_json(ctx.episode_dir/'repair_budget_grants.json',{}).get(cid,{}).get('upstream_recovery',{})
        if mode or input_operation(ctx,cid,proof['candidate'])['allowed']or not grant.get('input'):
            return route('verified_input',decision=proof['candidate'],evidence=proof)
    upstream=upstream_route(ctx,{})
    if upstream.get('kind')=='repack':return route('repack',decision=upstream['decision'])
    if not clips:return route('native_plan',targets=[])
    missing=[{'clip_id':c['clip_id'],'asset_id':r.get('asset_id'),'path':r['path']}
             for c in clips for r in c.get('references',[])if r.get('path')
             and not (ctx.novel_dir/r['path']).is_file()]
    if missing:return route('native_assets',missing_assets=missing)
    notes=read_json(ctx.episode_dir/'review_feedback.json',{})
    stale=[c['clip_id']for c in clips if c.get('kind')=='video'
           and(h3_prompt_outdated(c,notes.get(c['clip_id'],''),strict=True)or request_issues(c))]
    if stale:return route('native_requests',targets=stale)
    pending=sorted(unfilmed_video_clips(ctx.episode_dir,plan))
    if pending:return route('native_first_pass',targets=pending)
    ev=EpisodeEvidence(ctx)
    review=read_json(ctx.episode_dir/'episode_review.json',{})
    from novel_manga.application.review.execution import media_matches
    matching=media_matches(plan,media,notes=notes)
    if matching and(ev.unreviewed_clips(media,review)or not ev.current_review(review)):
        return route('review_existing',targets=ev.unreviewed_clips(media,review))
    if matching and (media.get('quality_review')or{}).get('passed')is True:
        return route('finish_existing')
    # Other confirmed defects enter the ordinary Agent path, not the asset-only filter.
    allowed=eligible_targets(ctx,ev)
    if not matching and media.get('clips') and not allowed:
        # A failed finish snapshots the current plan too. It cannot identify
        # which takes predate that plan; the existing renderer compares actual
        # saved requests, reuses matches and obeys the original video limits.
        return route('render_current',targets=[])
    if remaining>0 and allowed:
        window=saved.get('upstream_recovery')or{}
        if window.get('proposals',0)<2:return route('agent_repair',targets=sorted(allowed))
    if remaining<=0:return route('activity_exhausted',reason='No remaining activity time or newly verified recovery candidate',targets=sorted(allowed))
    return route('needs_diagnosis',reason='No current legal clip action; inspect preparation, actual requests and current media',
                 preparation=read_json(ctx.run_dir/'pipeline-production.json',{}),targets=sorted(allowed))


def execute_standard(row,novel_dir,out):
    """Normal stages do not borrow an exhausted Agent's creative allowance."""
    import os
    from novel_manga.util import atomic_write_json,load_dotenv
    from novel_manga.application.agents.episode import lifecycle,runtime
    from novel_manga.application.agents.episode.inputs import prepare_inputs
    from novel_manga.application.agents.episode.tools import EpisodeTools
    from novel_manga.application.agents.episode.recovery import remaining_at_stop,conversation_deferred
    from novel_manga.application.agents.episode.flow import run_episode
    from novel_manga.application.agents.sandbox import load_config
    from novel_manga.application.resolved_settings import resolve_agent_environment
    from novel_manga.application.repair import history
    root=Path(os.environ['NOVEL_PROJECT_ROOT']);load_dotenv(root/'.env')
    os.environ.update(row.get('environment',{}))
    os.environ.update(NOVEL_FIRST_PASS_ONLY='0',NOVEL_PRODUCTION_ONLY='0',PYTHONPATH=str(Path(__file__).resolve().parents[3]))
    ctx=context(row,novel_dir);config=load_config(Path(row['config']))
    before=history.generation_counts(ctx.episode_dir);kind=row['upstream_action']['kind']
    result={'episode':row['episode'],'run_dir':str(ctx.run_dir),'route':kind,'before_counts':before}
    final={'passed':False,'status':'held'}
    with lifecycle.episode_lock(ctx):
        prepare_inputs(ctx);env=resolve_agent_environment(ctx,config);env['NOVEL_AGENT_MANAGED']='1'
        with runtime.process_environment(env):
            tools=EpisodeTools(ctx);ready=True
            if kind=='native_plan':
                from novel_manga.application.agents.episode.pipeline_preparation import prepare
                result['preparation']=prepare(ctx,tools.drafts,tools.repairs)
                ready=result['preparation'].get('ready')is True
            elif kind=='native_assets':
                from novel_manga.application.agents.episode.pipeline_preparation import ensure_cards
                ensure_cards(ctx,read_json(ctx.episode_dir/'clip_plan.json'))
            if ready and kind in {'native_plan','native_assets','native_requests','native_first_pass'}:
                from novel_manga.application.agents.episode.local_preparation import ensure_requests
                result['request_blocks']=ensure_requests(ctx)
                if result['request_blocks']:
                    from novel_manga.application.agents.episode.pipeline_preparation import correct_requests
                    result['request_blocks']=correct_requests(ctx,tools.drafts,tools.repairs,result['request_blocks'])
                from novel_manga.application.preparation.store import unfilmed_video_clips
                if unfilmed_video_clips(ctx.episode_dir):
                    # Existing filmed clips remain cached; ordinary retakes belong to the Agent.
                    with runtime.process_environment({'NOVEL_FIRST_PASS_ONLY':'1'}):
                        result['render']=tools.dispatch('render',{})
            if ready and kind=='render_current':
                result['render']=tools.dispatch('render',{})
            if ready and kind!='agent_repair':
                result['review']=tools.dispatch('review',{})
                final=tools.dispatch('finish',{});result['finish']=final
        saved=read_json(ctx.run_dir/'agent-run.json',{})
    remaining=remaining_at_stop(saved)
    if final.get('passed')is not True and remaining>0:
        # Reuse remaining time, session and proposal counters. No new grant is made.
        for number in range(3):
            final=run_episode(ctx,config=config,timeout=max(1,int(remaining)),
                retry_issues=number==0 and saved.get('status')!='deferred')
            saved=read_json(ctx.run_dir/'agent-run.json',final);remaining=remaining_at_stop(saved)
            if not(conversation_deferred(final)and remaining>0):break
        result['slices']=number+1
    after=history.generation_counts(ctx.episode_dir)
    result.update(status=('finished'if final.get('passed')is True else final.get('status')or'held'),
        passed=final.get('passed')is True,reason=final.get('reason')or final.get('error'),after_counts=after,
        counts_preserved=all(after.get(cid,0)>=used for cid,used in before.items()))
    atomic_write_json(out/f"{row['episode']}.json",result)
    atomic_write_json(ctx.run_dir/'coverage-attempt.json',{'route':kind,'completed':True,
        'passed':result['passed'],'reason':result['reason']})
    return result
