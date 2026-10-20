"""Current failure diagnosis and outcome memory for the existing episode loop."""
from __future__ import annotations

import re
import time
import json
import fcntl
import copy
from functools import lru_cache
from pathlib import Path

from novel_manga.util import atomic_write_json, read_json
from novel_manga.llm.refusal import refusal_reason


LESSONS = {
    'speech_overflow': 'Too much speech for the fixed duration. Before adoption compress/split the draft. After source adoption use input shots[].turn_texts to shorten each original turn in order; preserve speakers and decisive meaning for source checking. Another identical take cannot make the words fit.',
    'speech': 'Inspect raw audio and ASR. Homophones, names and nonverbal laughter are not automatically missing or invented dialogue. Recheck before spending a take.',
    'picture': 'Compare the current selected phase image and source against actual frames. Minor color/styling differences are advisory; wrong actor/action or a missing story event needs repair.',
    'input': 'Fix the earliest source/reference mismatch. Missing actors use characters; an existing actor uses phases. Include shared-shot dependent clips and reuse already generated assets.',
    'publication_gate': 'Final acceptance named these clips even if the ordinary review passed. They remain eligible repair targets; do not claim the episode is done.',
    'technical': 'Locate the actual failing time interval in the final timeline. end_seconds can be the measurement-window end, not the failed interval.',
    'arguments': 'Correct the tool fields/scope reported by check; repeating resume cannot fix malformed arguments.',
    'execution': 'The call or result protocol failed, so no content judgment was obtained. Preserve the candidate and use the current technical continuation; do not rewrite source content on this evidence.',
}


def diagnose(ctx, status):
    plan = read_json(ctx.episode_dir/'clip_plan.json', {})
    clips = {c['clip_id']: c for c in plan.get('clips', [])}
    evidence = read_json(ctx.work_dir/'input/latest-review.json', {})
    current_review = read_json(ctx.episode_dir/'episode_review.json', {})
    current_media = read_json(ctx.episode_dir/'thin_media_report.json', {})
    selected = {r['clip_id']: r.get('selected') or {} for r in current_media.get('clips') or []}
    issues = []

    def add(kind, ids, detail):
        if 'first_pass_remaining' in status and ids and not set(ids)&set(status['first_pass_remaining']):
            return
        if kind in {'input', 'arguments'} and re.match(
                r'^(?:RequestCheckIncomplete|SourceCheckIncomplete|LLMFailure|ConnectError|ConnectTimeout|ReadTimeout|ConnectionError|TimeoutError|JSONDecodeError):|'
                r'^ValueError: (?:verification requires|verification flags require|count review)', str(detail)):
            kind = 'execution'
        issues.append({'kind': kind, 'clips': list(ids), 'detail': str(detail)[:700],
                       'next': LESSONS[kind]})

    for target in evidence.get('targets') or []:
        current = target.get('clip') or {}
        cid = current.get('clip_id')
        clip = clips.get(cid, current)
        audio = selected.get(cid, target.get('selected_audio') or {})
        if audio.get('passed') is False:
            text = str(clip.get('spoken_text') or audio.get('reference') or '')
            count = len(re.findall(r'[\u3400-\u9fff]', text))
            seconds = float(clip.get('request_seconds') or 0)
            overflow = seconds > 0 and count > seconds * 6
            add('speech_overflow' if overflow else 'speech', [cid],
                f'{count} Chinese characters / {seconds:g}s; {audio.get("issues", [])}')
        verdict = (current_review.get('clips') or {}).get(cid, target.get('verdict') or {})
        if verdict.get('severity') == 'fail':
            add('picture', [cid], verdict.get('feedback') or verdict.get('story_issue') or verdict.get('defect_issue'))
        block = ((current_media.get('blocked_clips') or {}).get(cid) if current_media
                 else target.get('input_block'))
        if block:
            add('input', [cid], block)
    if not status.get('adopted') or current_media.get('status') == 'inputs_failed':
        exception = read_json(ctx.work_dir/'input/preparation/exception.json', {}).get('failure') or {}
        preparation = read_json(ctx.work_dir/'input/latest-preparation.json', {})
        current = (preparation.get('source_audit') if preparation else exception.get('audit')) or {}
        for error in current.get('issues') or []:
            add('input', [], error)
        for cid, error in (preparation.get('blocked') or {}).items():
            add('input', [cid], error)
        if not preparation and not current.get('issues') and exception.get('reason'):
            add('input', [], exception['reason'])
        draft = read_json(ctx.work_dir/'input/source-draft-check.json', {})
        for error in draft.get('errors') or []:
            add('input', [error.get('clip_id')] if isinstance(error, dict) and error.get('clip_id') else [], error)
    for cid, repair in (status.get('input_repairs') or {}).items():
        if repair.get('error'):
            add('input', [cid], repair['error'])
    job = status.get('job') or {}
    result = job.get('result') or {}
    if result.get('error') == 'publication_gate':
        add('publication_gate', result.get('clips') or [], result.get('message', 'Final review not passed'))
    elif status.get('publication_blockers'):
        add('publication_gate', status['publication_blockers'], 'Current precise publication review is unresolved')
    for cid, error in (result.get('argument_errors') or {}).items():
        add('arguments', [cid], error)
    error = result.get('error')
    if error and str(error).startswith(('ValueError:', 'RepairArgumentError:')):
        add('arguments', [], error)
    technical = (status.get('media') or {}).get('technical_issues') or {}
    if technical:
        add('technical', [], technical)
    actions = status.get('repair_actions') or {}
    return {'issues': issues, 'actionable': any(actions.values()) or (
        not status.get('adopted') and bool(issues)),
        'instruction': 'Diagnose the first wrong artifact and act through the listed tools. '
        'Use current permissions. Record stop only for an explicit exhausted allowance, missing source or unavailable capability.'}


def _memory_dir(ctx):
    return ctx.source_novel_dir/'.production-memory'


def remember(ctx, diagnosis, result, decisions=()):
    """One writer per run; an attempted remedy never becomes a verified success."""
    from novel_manga.application.preparation.store import has_video
    post_render = has_video(ctx.episode_dir)
    manifest=read_json(ctx.work_dir/'input/manifest.json',{})
    repair_phase=manifest.get('agent_phase') in {'input_repair','review'}
    if ((manifest.get('memory_scope') == 'post_render_repair' and not post_render)
            or (manifest.get('memory_scope') == 'repair_only' and not repair_phase)):
        return
    path = _memory_dir(ctx)/(ctx.run_dir.name+'.json')
    previous = read_json(path, {})
    issues = list(previous.get('issues') or [])
    for issue in diagnosis.get('issues') or []:
        if issue not in issues:
            issues.append(issue)
    if not issues:
        return
    outcome = ('accepted' if result.get('action') == 'finish' and result.get('ok') is True
               and result.get('passed') is True else 'unresolved')
    attempts = previous.get('attempts') or []
    attempt = {'action': result.get('action'), 'ok': result.get('ok'),
               'issues': copy.deepcopy(diagnosis.get('issues') or []),
               'changed': result.get('changed') or result.get('media_changed') or result.get('input_repaired') or [],
               'accepted_clips': result.get('accepted') or [],
               'blocked': result.get('blocked') or result.get('error'),
               'remedies': [{'action': row.get('action'), 'clip': row.get('clip_id'),
                            'reason': str(row.get('reason') or '')[:700],
                            'decision': copy.deepcopy(row)} for row in decisions]}
    if not attempts or attempts[-1] != attempt:
        attempts.append(attempt)
    record={'episode': ctx.source_episode_dir.name, 'run_dir': str(ctx.run_dir),
        'scope':'post_render_repair' if post_render else 'semantic_input_repair' if repair_phase else 'legacy_preparation',
        'evidence':str(ctx.episode_dir/'episode_review.json'),
        'profile':read_json(ctx.novel_dir/'profile.json',{}) if hasattr(ctx,'novel_dir') else {},
        'updated_at': time.time(), 'issues': issues, 'outcome': outcome, 'attempts': attempts}
    atomic_write_json(path,record)
    if post_render or repair_phase:
        document_memory(path,record)


def document_memory(path, record):
    """Short factual cases plus one serialized index; no extra model summary."""
    lines=[f"# {record['episode']} — 审修记录",'',f"结果：{record['outcome']}",
           '适用范围：当前原文、素材状态和画风；未通过复核的尝试仅用于避免重复。',
           f"证据：{record['evidence']}",'','问题与证据：','']
    for issue in record['issues']:
        lines.append(f"- {issue['kind']} / {','.join(issue.get('clips') or [])}: {issue.get('detail','')}")
    from .memory_summary import summarize_case
    summary = summarize_case(record)
    lines += ['', '修法摘要（changed仅证明操作生效，accepted_clip证明该片段复核通过）：', '']
    for operation in summary['effective_operations']:
        lines.append('- '+json.dumps(operation, ensure_ascii=False))
    if not summary['effective_operations']:
        lines.append('- 保留记录没有可还原的具体生效修法，不能从整集finish通过推断修法。')
    lines += ['', '失败尝试（不得当成成功修法）：', '']
    for failure in summary['failed_operations']:
        lines.append('- '+json.dumps(failure, ensure_ascii=False))
    lines+=['','已执行的修法与结果：','']
    for attempt in record['attempts']:
        lines.append('- '+json.dumps(attempt,ensure_ascii=False))
    target=path.with_suffix('.md');temporary=target.with_suffix('.md.tmp')
    temporary.write_text('\n'.join(lines)+'\n');temporary.replace(target)
    with (path.parent/'index.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        rows=[]
        for case in sorted(path.parent.glob('*.json')):
            saved=read_json(case,{})
            if saved.get('scope') not in {'post_render_repair','semantic_input_repair'}:continue
            kinds=', '.join(sorted({r['kind'] for r in saved.get('issues',[])}))
            rows.append(f"- {kinds} | {saved.get('outcome')} | [{saved.get('episode')}]({case.stem}.md)")
        temporary=path.parent/'index.md.tmp'
        temporary.write_text('# 跨集审修经验索引\n\naccepted 表示整集正式复核通过；unresolved 记录尝试，不能当成有效修法。\n\n'+'\n'.join(rows)+'\n')
        temporary.replace(path.parent/'index.md')


def recall(ctx, diagnosis):
    kinds = {i['kind'] for i in diagnosis.get('issues') or []}
    if not kinds:
        return []
    rows = []
    notes = {}
    for summary_path in sorted(_memory_dir(ctx).glob('summaries/*/summary.json')):
        for case in read_json(summary_path, {}).get('cases') or []:
            if case.get('source_memory'):
                notes[Path(case['source_memory']).name] = {
                    'source': str(summary_path),
                    'case_summary': str(case.get('case_summary') or '')[:400],
                    'lesson_and_limits': str(case.get('lesson_and_limits') or '')[:400]}
    review_scope = read_json(ctx.work_dir/'input/manifest.json',{}).get('memory_scope') in {'post_render_repair','repair_only'}
    for path in _memory_dir(ctx).glob('*.json'):
        if path.name == ctx.run_dir.name+'.json':
            continue
        record = read_json(path, {})
        if review_scope and record.get('scope') not in {'post_render_repair','semantic_input_repair'}:
            continue
        shared = kinds & {i['kind'] for i in record.get('issues') or []}
        if shared:
            rows.append({'episode': record.get('episode'), 'kinds': sorted(shared),
                         'outcome': record.get('outcome'),
                         'issues': [i for i in record.get('issues') or [] if i['kind'] in shared],
                         'attempts': record.get('attempts') or [],
                         'retrospective': notes.get(path.name),
                         'updated_at': record.get('updated_at', 0)})
    from .memory_summary import summarize_case
    rows.sort(key=lambda r: (bool(summarize_case(r)['effective_operations']),
                            r['outcome'] == 'accepted', r['updated_at']), reverse=True)
    return rows[:4]


def summary_ending(text):
    text = str(text or '').strip()
    return text.startswith(('<analysis>', '<summary>')) and text.endswith('</summary>')


@lru_cache(maxsize=256)
def completed_summary(directory):
    """Completed attempt records are immutable; legacy records retain CLI output."""
    directory = Path(directory)
    record = read_json(directory/'attempt.json', {})
    if 'summary_only' in record:
        return record['summary_only']
    path = directory/'session.jsonl'
    if not path.is_file():
        return False
    completion = {}
    for line in path.open():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'result':
            completion = event
    return summary_ending(completion.get('result'))


def remaining_at_stop(saved):
    stored = saved.get('remaining_seconds')
    if saved.get('deadline') and saved.get('updated_at'):
        stopped = time.mktime(time.strptime(saved['updated_at'], '%Y-%m-%d %H:%M:%S'))
        derived = max(0, float(saved['deadline'])-stopped)
        return min(float(stored), derived) if stored is not None else derived
    return max(0, float(stored or 0))


def conversation_deferred(saved):
    return(saved.get('status')=='deferred' and saved.get('phase')=='deferred'
        and str(saved.get('reason')or'').startswith(('Agent slice completed','Agent reached this slice time limit')))


@lru_cache(maxsize=256)
def completed_idle(directory):
    """Observed output/handoff facts, not the wording of the final answer."""
    folder=Path(directory);record=read_json(folder/'attempt.json',{})
    if record.get('exit')!=0 or record.get('produced')!=[]:return False
    path=folder/'session.jsonl'
    if not path.is_file():return False
    for line in path.open():
        try:event=json.loads(line)
        except ValueError:continue
        if event.get('type')!='user':continue
        for item in (event.get('message') or {}).get('content') or []:
            if not isinstance(item,dict) or item.get('type')!='tool_result':continue
            content=item.get('content')
            if not isinstance(content,str):continue
            for part in content.splitlines():
                try:value=json.loads(part)
                except ValueError:continue
                if isinstance(value,dict) and value.get('yield') and value.get('job_id'):
                    return False
    return True


def resume_protocol(run_dir,episode_dir,saved, *, validate_input=None):
    """One continuation after a diagnosed interface fix, using unspent time only."""
    if (saved.get('status')!='blocked' or saved.get('passed') or saved.get('protocol_recovery')
            or remaining_at_stop(saved)<=0 or (saved.get('job') or {}).get('status') in {'accepted','running'}):
        return None
    run_dir,episode_dir=Path(run_dir),Path(episode_dir)
    if read_json(run_dir/'final-candidate/manifest.json',{}).get('passed') is True:return None
    from .input_records import latest_input_records
    from novel_manga.application.repair import history
    from novel_manga.application.preparation.store import unfilmed_video_clips
    from novel_manga.application.preparation.readiness import render_risk_choices,risk_acceptances
    from types import SimpleNamespace
    plan=read_json(episode_dir/'clip_plan.json',{})
    clips={c['clip_id']:c for c in plan.get('clips',[])}
    missing=unfilmed_video_clips(episode_dir,plan)
    counts=history.generation_counts(episode_dir)
    def available(cid):
        return (cid in clips and (not missing or cid in missing)
                and counts.get(cid,0)<history.preparation_limit(episode_dir,cid))
    ctx=SimpleNamespace(run_dir=run_dir,episode_dir=episode_dir,novel_dir=episode_dir.parent,
        work_dir=run_dir/'sandbox',input_mode=saved.get('input_mode','source'))
    def valid_input(row):
        if validate_input is None:return False
        try:
            answer=validate_input(ctx,row,check_only=True)
            return all(available(cid) for cid in answer.get('clip_ids',[row['clip_id']]))
        except (ValueError,KeyError,TypeError,OSError):return False
    outcome=(saved.get('job') or {}).get('result') or {}
    failures={**(outcome.get('blocked') or {}),**(outcome.get('argument_errors') or {})}
    kind=None
    for cid,error in failures.items():
        if 'risk acceptance must name current wearer/location reference risks' in str(error) and available(cid):
            choices=render_risk_choices(episode_dir,clips[cid])
            accepted=risk_acceptances(episode_dir,{'clips':[clips[cid]]})[cid]
            if any(set(v)-set(accepted.get(k,[])) for k,v in choices.items()):
                kind='risk_arguments';break
    if kind is None:
        records=latest_input_records(episode_dir)
        for cid,(_,record) in records.items():
            if (not available(cid) or record.get('status') not in {'checking','incomplete'}
                    or 'input repair changed clip grouping' not in str(record.get('error',''))
                    or (failures and 'input repair changed clip grouping' not in str(failures.get(cid,'')))):
                continue
            if valid_input({'clip_id':cid,'action':'input','resume':True,'reason':'Continue saved input with precise compiler diagnostics'}):
                kind='compile_diagnostics';break
    attempts=saved.get('attempts') or []
    if kind is None and attempts:
        proposal=read_json(Path(attempts[-1])/'output-after/repair.json',{})
        for row in proposal.get('decisions',[]):
            if (not isinstance(row,dict) or row.get('resume') is not True
                    or not set(row)-{'clip_id','action','resume','reason'}
                    or row.get('clip_id') not in failures or not available(row['clip_id'])):continue
            # A correction must still be affordable and legal as a NEW proposal.
            if valid_input({k:v for k,v in row.items() if k!='resume'}):
                kind='resume_arguments';break
    if (kind is None and not failures and not saved.get('model_switch') and len(attempts)>=2
            and saved.get('reason') in {'episode agent ended twice without operation or output progress',
                                       'episode agent reached the bounded operation continuation limit'}
            and all(completed_idle(str(p)) for p in attempts[-2:])):
        kind='idle_turns'
    if kind is None:return None
    remaining=remaining_at_stop(saved)
    marker={'kind':kind,'at':time.time()}
    atomic_write_json(run_dir/'protocol-recovery.json',{'previous':saved,'recovery':marker})
    resumed={**saved,'status':'deferred','phase':'ready_agent','protocol_recovery':marker,
        'reason':'Continue with corrected repair interface and current task evidence',
        'remaining_seconds':remaining,'deadline':time.time()+remaining,'no_progress':0,
        'fallback_invocations':saved.get('fallback_invocations',0),
        'job':None,'updated_at':time.strftime('%F %T')}
    atomic_write_json(run_dir/'agent-run.json',resumed)
    return resumed


def protocol_fallback(ctx, config, saved):
    # An idle completion is not a provider failure. Keep the primary model and
    # refresh context once under the existing protocol recovery marker.
    marker = saved.get('protocol_recovery') or {}
    if marker.get('kind') != 'idle_turns' or config.get('context_restart'):
        return config, saved
    return {**config, 'context_restart': 'idle-protocol-' + str(marker.get('at'))}, saved


def resume_source_review(run_dir, episode_dir, saved):
    """Revisit old source-only rejections once after the judge fix, without a retry grant."""
    from .input_records import SOURCE_REVIEW_POLICY, latest_input_records, source_review_recoverable
    if (saved.get('status') not in {'blocked', 'finished_with_issues'} or saved.get('passed')
            or saved.get('source_review_recovery')
            or (saved.get('job') or {}).get('status') in {'accepted', 'running'}):
        return None
    remaining = remaining_at_stop(saved)
    if remaining <= 0:
        return None
    records = [str(path) for path, record in latest_input_records(episode_dir).values()
               if source_review_recoverable(record)]
    if not records:
        return None
    run_dir = Path(run_dir)
    marker = {'policy': SOURCE_REVIEW_POLICY, 'records': records, 'at': time.time()}
    resumed = {**saved, 'source_review_recovery': marker, 'status': 'deferred', 'phase': 'ready_agent',
               'reason': 'Resume saved source proposals under the corrected source judge',
               'no_progress': 0, 'fallback_invocations': 0, 'stop_detail': None, 'job': None,
               'remaining_seconds': remaining, 'deadline': time.time()+remaining,
               'updated_at': time.strftime('%F %T')}
    atomic_write_json(run_dir/'source-review-recovery.json', {**marker, 'previous': saved})
    atomic_write_json(run_dir/'agent-run.json', resumed)
    return resumed


def blocked_summary_loop(run):
    saved = read_json(Path(run)/'agent-run.json', {})
    attempts = saved.get('attempts') or []
    return (saved.get('status') == 'blocked' and not saved.get('summary_context_recovery')
            and not saved.get('protocol_recovery')
            and remaining_at_stop(saved) > 0
            and saved.get('reason') in {
                'episode agent ended twice without operation or output progress',
                'episode agent reached the bounded operation continuation limit'}
            and len(attempts) >= 2 and all(completed_summary(str(p)) for p in attempts[-2:]))


def resume_summary_loop(ctx, config, saved):
    """Reopen only an observed loop, using its unspent active time and files."""
    if not blocked_summary_loop(ctx.run_dir):
        return None
    marker = {'at': time.time()}
    resumed = {**saved, 'summary_context_recovery': marker}
    replacement = {**config, 'context_restart': config.get('context_restart') or 'summary-context-' + str(marker['at'])}
    remaining = remaining_at_stop(saved)
    resumed.update(status='deferred', reason=None, stop_detail=None,
                   remaining_seconds=remaining, deadline=time.time()+remaining)
    atomic_write_json(ctx.run_dir/'agent-run.json', resumed)
    return replacement, resumed


def switch_for_refusal(ctx, config, result, attempt, before, after, *, remaining=None):
    handed_off = (after.get('job') or {}).get('id') != (before.get('job') or {}).get('id')
    result['summary_only_turns'] = (result.get('summary_only_turns', 0)+1
                                  if attempt.record.get('summary_only') and not handed_off else 0)
    idle=(attempt.record.get('exit')==0 and attempt.record.get('produced')==[] and not handed_off)
    result['idle_turns']=result.get('idle_turns',0)+1 if idle else 0
    from .limits import MAX_INVOCATIONS
    from .bounded_recovery import invocation_limit_reached
    count = result.get('fallback_invocations', 0)
    reason = attempt.record.get('refusal')
    # Reserve the last existing continuation for fallback, rather than opening
    # another retry budget after exhausting all primary attempts.
    if (not reason and attempt.record.get('failure_kind') in {'context_compaction', 'transient_api'}
            and count >= MAX_INVOCATIONS - 2
            and any(row.get('activity') == activity(result)
                    for row in result.get('invocation_retry_history', []))):
        reason = 'Primary technical retries failed: ' + attempt.record['failure_kind']
    fallback = config.get('refusal_fallback')
    if (not fallback or result.get('model_switch') or not reason or handed_off
            or config.get('model') == fallback.get('model')
            or count + 1 >= MAX_INVOCATIONS or invocation_limit_reached(result)
            or (remaining is not None and remaining <= 0)):
        return None
    if not all(fallback.get(k) for k in ('model', 'base_url', 'key_var')):
        return None
    archive = ctx.run_dir/'model-switch'/str(time.time_ns())
    archive.mkdir(parents=True, exist_ok=True)
    session = ctx.work_dir/'episode-session.json'
    if session.exists():
        session.rename(archive/'episode-session.json')
    record = {'policy': 'activity-failure-v1', 'activity': activity(result),
              'from_model': config.get('model'), 'to_model': fallback['model'],
              'attempt': str(attempt.directory), 'reason': reason,
              'deadline': result.get('deadline'), 'at': time.time()}
    atomic_write_json(archive/'switch.json', record)
    result.update(model_switch=record, no_progress=0, fallback_invocations=count+1, summary_only_turns=0,idle_turns=0)
    return {**config, 'model_slots_reserved_for_tools': 0, **fallback}



def upstream_route(ctx,status):
    """Current source-level route; it is a proposal, never an automatic acceptance."""
    from .bounded_recovery import evidence,input_operation
    plan=read_json(ctx.episode_dir/'clip_plan.json',{})
    source=(ctx.run_dir/'source.txt').read_text()if(ctx.run_dir/'source.txt').is_file()else ''
    long=[c['clip_id']for c in plan.get('clips',[])if c.get('kind')=='video'
          and c.get('request_seconds',0)>ctx.clip_seconds_max]
    if long:
        return {'kind':'repack','decision':{'clip_id':long[0],'action':'repack','clip_ids':long,
            'reason':'Current saved ranges exceed H3 clip limit','source_quote':source[:180]}}
    for clip in plan.get('clips',[]):
        cid=clip['clip_id']
        proof=evidence(ctx,cid)
        if proof and input_operation(ctx,cid,proof['candidate'])['allowed']:
            return {'kind':'input_recovery','decision':proof['candidate'],'evidence':proof}
    records=sorted((ctx.episode_dir/'work/input_repairs').glob('clip_*-*.json'),key=lambda p:p.stat().st_mtime_ns)
    for path in reversed(records):
        r=read_json(path,{})
        if r.get('operation')=='upstream_asset':continue
        row=r.get('decision')or{}
        if r.get('status')not in {'incomplete','rejected'} or not row.get('source_quote') or row['source_quote']not in source:continue
        clip=next((c for c in plan.get('clips',[])if c['clip_id']==r.get('clip_id')),None)
        if not clip:continue
        people=(row.get('phases')or{}).get('characters')or row.get('characters')or{}
        locations=row.get('locations')or{}
        for ref in clip.get('references',[]):
            fields=None
            if ref.get('role')=='character' and ref.get('name')in people:
                value=people[ref['name']];value=value[0]if isinstance(value,list)else value
                if value.get('reuse_asset_id'):continue
                from novel_manga.application.identity.phases import LOOK_FIELDS
                fields={k:v for k,v in value.items()if k in LOOK_FIELDS}
            elif ref.get('role')=='location' and ref.get('name')in locations:fields={'description':locations[ref['name']]}
            if fields:
                return {'kind':'asset','decision':{'clip_id':clip['clip_id'],'action':'repair_assets',
                    'asset_id':ref['asset_id'],'reason':row['reason'],'source_quote':row['source_quote'],'fields':fields}}
    return {'kind':'episode_repair' if any(status.get('repair_actions',{}).values())else 'needs_evidence',
            'reason':'Use current legal actions; no new source-supported upstream candidate was retained.'}


def activity(result):
    return {'started': result.get('agent_activity'),
            'authorization': (result.get('upstream_recovery') or {}).get('authorization')}


def restore_model_route(config, saved):
    """Only this activity's observed failure may select the configured fallback."""
    marker = saved.get('model_switch')
    fallback = config.get('refusal_fallback') or {}
    if not marker:
        return config, saved
    if (marker.get('policy') == 'activity-failure-v1'
            and marker.get('activity') == activity(saved)
            and marker.get('from_model') == config.get('model')
            and marker.get('to_model') == fallback.get('model')):
        return {**config, 'model_slots_reserved_for_tools': 0, **fallback}, saved
    saved = dict(saved)
    saved['model_switch_history'] = [*saved.get('model_switch_history', []), saved.pop('model_switch')]
    # Session policy/activity/model matching rotates obsolete conversations once.
    return config, saved


def retry_failed_invocation(config, result, attempt, remaining):
    """Retry technical CLI failures within the existing shared continuation limits."""
    from .limits import MAX_INVOCATIONS
    from .bounded_recovery import invocation_limit_reached
    kind = attempt.record.get('failure_kind')
    count = result.get('fallback_invocations', 0)
    if (kind not in {'context_compaction', 'transient_api'} or remaining <= 0
            or count + 1 >= MAX_INVOCATIONS or invocation_limit_reached(result)):
        return None
    result['fallback_invocations'] = count + 1
    result.setdefault('invocation_retry_history', []).append({
        'attempt': str(attempt.directory), 'kind': kind, 'activity': activity(result),
        'deadline': result['deadline'], 'at': time.time()})
    replacement = dict(config)
    if kind == 'context_compaction':
        # Restart only conversational context. Saved work, deadline and grants
        # remain authoritative, and the next invocation is charged normally.
        replacement['context_restart'] = f"cli-context-retry-{attempt.directory.name}"
    return replacement
