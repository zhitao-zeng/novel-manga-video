"""A current repair view; complete state and history remain available on disk."""
from __future__ import annotations

from novel_manga.util import atomic_write_json,read_json
from .memory_summary import summarize_case


def prepare(ctx,status,diagnosis,memories):
    plan=read_json(ctx.episode_dir/'clip_plan.json',{})
    by_id={c['clip_id']:c for c in plan.get('clips',[])}
    adopted=bool(status.get('adopted',bool(by_id)))
    actions=status.get('repair_actions') or {}
    selected=set(status['first_pass_remaining']) if 'first_pass_remaining' in status else (
        {cid for cid,ops in actions.items() if ops} | set(status.get('unreviewed_clips') or [])
        | set(status.get('publication_blockers') or []))
    targets=[]
    from .requests import dialogue_slots
    for cid in sorted(selected):
        clip=by_id.get(cid,{})
        saved=(status.get('input_repairs') or {}).get(cid) or {}
        targets.append({'clip_id':cid,'shot_indexes':clip.get('shot_indexes',[]),
            'dialogue_slots':dialogue_slots(clip),
            'request_seconds':clip.get('request_seconds'),
            'references':[{k:r[k] for k in ('name','role','asset_id','image') if k in r}
                          for r in clip.get('references',[])],
            'actions':actions.get(cid,[]),
            'upstream_actions':(status.get('upstream_actions')or{}).get(cid,[]),
            'risk_choices':(status.get('risk_choices') or {}).get(cid,{}),
            'remaining_takes':(status.get('remaining_takes') or {}).get(cid),
            'input':{'operations':(status.get('input_operations') or {}).get(cid, {}),
                'remaining_attempts':(status.get('remaining_input_attempts') or {}).get(cid),
                'resume_saved':saved.get('resume_action'),
                'can_revise_saved':bool(saved.get('revision_available')),
                'failed_step':saved.get('step'),'error':saved.get('error'),
                'error_details':saved.get('error_details'),'next':saved.get('next_action')}})
    issues=[r for r in diagnosis.get('issues',[]) if not status.get('adopted')
            or not r.get('clips') or selected.intersection(r['clips'])]
    files=[f'input/{name}' for name in ('source.txt','segments.json','chapter_script.json',
        'clip_plan.json','catalog.json','latest-preparation.json','latest-render.json','latest-review.json',
        'preparation/exception.json','source-draft-check.json','request-draft.json','manifest.json')
        if (ctx.work_dir/'input'/name).is_file()]
    if not adopted and (ctx.work_dir/'output/episode.json').is_file():files.append('output/episode.json')
    recent=[summarize_case(memory) for memory in memories[:2]]
    job=status.get('job') or {}
    outcome=job.get('result') or {}
    last={k:job.get(k) for k in ('id','action','status','error')}
    last['result']={k:outcome[k] for k in ('ok','passed','error','changed','input_repaired','input_reused','unresolved_clips') if k in outcome}
    if 'pending'in outcome and job.get('action')=='apply' and job.get('status')=='completed':
        last['result']['unresolved_clips']=outcome['pending']
    for field in ('blocked','argument_errors'):
        if isinstance(outcome.get(field),dict):last['result'][field]={k:v for k,v in outcome[field].items() if k in selected}
    brief={'phase':read_json(ctx.work_dir/'input/manifest.json',{}).get('agent_phase'),
        'mode':'adopted_repair' if adopted else 'provisional_draft',
        'upstream_route':status.get('upstream_route'),
        'targets':targets,'issues':issues,'last_operation':last,'evidence_files':files,
        'full_status':'input/current-status.json','reference_catalog':'input/catalog.json','related_history':recent,
        'tool_source':'/opt/novel-code/novel_manga/application/agents/episode',
        'execution_rules':([
            'Only these current targets need work. Old errors on other clips are history, not new tasks.',
            'Write output/repair.json and call check, then apply. Replying with JSON alone executes nothing.'] if adopted else [
            'No adopted plan exists. Repair the saved provisional output/episode.json using the planner failure and source; call check, then adopt.',
            'For missing draft assets only, use output/repair.json with source-backed asset requests and apply; bind returned IDs in the draft.',
            'Empty adopted targets do not mean the draft is complete. Read the saved draft/check evidence.'])+[
            'resume:true executes the saved decision unchanged; do not combine it with new shots/assets/source_quote.',
            'remaining_attempts does not renew a spent revision of the same pending candidate.',
            'Use exact risk_choices values. Accepted tool job means submitted; inspect its completed result.',
            'unresolved_clips (pending in old completed apply receipts) are remaining repair targets, not running jobs. When busy=false and '
            'job.status=completed, take the next legal action or finish; do not wait for these clips to change on their own.',
            'Related history distinguishes changed operations, independently accepted clips and failed attempts. '
            'An accepted episode does not prove every attempted remedy worked. Match the concrete issue to current evidence; '
            'not_recorded means no repair recipe was retained, and unresolved cases are not verified recipes.',
            'repair_assets/repack are separate upstream operations; source/image candidates do not grant video or certify acceptance.',
            'For deterministic tool bugs, inspect the read-only source and report the concrete failing field; do not repeat the same rejected operation.']}
    playbook=read_json(ctx.work_dir/'input/manifest.json',{}).get('skill_references',{}).get('repair_playbook')
    if playbook:
        brief['repair_playbook']=playbook
        brief['execution_rules'].append('For review/input repair, read repair_playbook if it is new to this session; '
            'then use the section matching current evidence and capabilities. A resumed old conversation also receives this route.')
    atomic_write_json(ctx.work_dir/'input/current-status.json',status)
    atomic_write_json(ctx.work_dir/'input/current-task.json',brief)
    return brief
