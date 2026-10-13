"""Codex Luna authoring; existing host tools alone adopt/render/review/finish."""
import json, os, shutil, tempfile, time
from pathlib import Path
from luna_transport import MODEL, CODEX, run, write

def run_session(work_dir,prompt_text,*,resume=False,timeout=1200,config=None,expected_output=None,enable_tools=False,project_root=None):
    from novel_manga.application.agents.sandbox import Attempt,unique_attempt
    from novel_manga.application.agents.episode import session,client,recovery
    from novel_manga.util import read_json
    from novel_manga.llm import usage
    from novel_manga.application.agents.sandbox import SandboxRefused
    if not prompt_text.strip() or timeout <= 0:
        raise SandboxRefused('The episode session needs a prompt and positive timeout.')
    expected_output=expected_output or ('repair.json' if resume and not enable_tools else 'episode.json')
    if expected_output not in {'episode.json', 'requests.json', 'repair.json'}:
        raise SandboxRefused('Unsupported episode output name.')
    config=config or {};work=Path(work_dir).resolve();output=work/'output';output.mkdir(exist_ok=True)
    attempt=unique_attempt(work/'attempts',time.strftime('%Y%m%d-%H%M%S')+'-codex-luna')
    state_path=work/session.SESSION_FILE;old=read_json(state_path,{})
    write(attempt/'previous-session.json',old)
    skills=work/'.claude/skills/episode-production'
    shutil.copytree(session.SKILL_SOURCE,skills,dirs_exist_ok=True)
    # Short Unix socket path; production edits remain exclusively through host tools.
    alias=Path(tempfile.mkdtemp(prefix='episode-luna-'))/'work';alias.symlink_to(work,target_is_directory=True)
    tool=work/'tools/codex_episode.py';tool.parent.mkdir(exist_ok=True)
    tool.write_text(Path(client.__file__).read_text().replace("Path('/work/episode-tools.sock')",f'Path({str(alias / "episode-tools.sock")!r})'))
    state={'current_task_policy':session.CURRENT_TASK_POLICY,
           'skill_revision':read_json(work/'input/manifest.json',{}).get('skill_revision'),
           'agent_instructions':config.get('agent_instructions'),'context_model':MODEL,'agent_activity':config.get('agent_activity'),
           'last_context_restart':config.get('context_restart'),'backend':'codex-chatgpt-cli','previous_session_id':old.get('session_id')}
    def event_callback(event):
        if event.get('type')=='thread.started':
            state['session_id']=event['thread_id'];write(state_path,state)
    before={str(p.relative_to(output)):(p.stat().st_mtime_ns,p.stat().st_size) for p in output.rglob('*') if p.is_file()}
    for p in output.rglob('*'):
        if p.is_file() and p.suffix in ('.json','.txt','.md'):
            dest=attempt/'output-before'/p.relative_to(output);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
    role=(f'You are the authorized episode repair agent, running natively with Codex Luna. Workspace: {alias}. '
          f'Your writable directory is {output}. Production inputs, host state and histories are read-only to you. '
          'Use the existing episode-production skill and host tools; do not implement new tools or call model/H3 services yourself. '
          f'Read {alias}/input/current-task.json and relevant target evidence first; consult {alias}/.claude/skills/episode-production/SKILL.md and its referenced contract as needed. '
          f'The existing host client is: python3 {alias}/tools/codex_episode.py ACTION [FILE]. '
          'The client accepts only relative file paths like output/repair.json. This is the same approved check/adopt/render/review/apply/status/finish interface. '
          'Any /work paths in legacy documentation or JSON mean the workspace above. Map them before file access; do not create /work. '
          'Write candidates only under output; do not edit input, episode-session.json, budget files, host state, source code or prior histories. '
          'When a host tool returns yield=true or accepted job_id, END THIS TURN immediately. The host starts the job after you exit and will resume you with its result. '
          'Never poll or sleep waiting for that job inside this turn. An accepted job is not completed. '
          'The current Skill and host decide acceptance and budgets. '
          'Do not mark passed yourself. '
          'This is a fresh compact context. Prior file evidence and host results remain available; do not try to resume a Claude session.\n\n')
    if not enable_tools:
        role += 'Host workflow tools are disabled for this call; only write the requested candidate file. '
    prompt=role+prompt_text.replace('/work/',str(alias)+'/').replace('/tools/episode.py','/tools/codex_episode.py')
    command=[CODEX,'exec','--ignore-user-config','--ephemeral','--skip-git-repo-check','--sandbox','workspace-write',
             '-c','sandbox_workspace_write.network_access=true','-m',MODEL,'-c','model_reasoning_effort=medium',
             '--json','--output-last-message',str(attempt/'answer.txt'),'-']
    try:
        result=run(command,attempt,prompt=prompt,timeout=timeout,cwd=work,event_callback=event_callback)
    finally:
        alias.unlink()
        alias.parent.rmdir()
    if result['exit_code'] == 0 and not result['completed']:
        result['exit_code'] = 1
    if result.get('thread_id') and state.get('session_id')!=result['thread_id']:
        state['session_id']=result['thread_id']
    usage.record(name='episode-agent',model=MODEL,usage={**result['usage'],'cache_read_input_tokens':result['usage'].get('cached_input_tokens',0)},url='codex://chatgpt-cli')
    produced=[str(p.relative_to(output))for p in output.rglob('*') if p.is_file() and before.get(str(p.relative_to(output)))!=(p.stat().st_mtime_ns,p.stat().st_size)]
    final=(attempt/'answer.txt').read_text() if (attempt/'answer.txt').exists() else ''
    expected_output=expected_output or 'repair.json'
    error_text=json.dumps(result['errors']).lower()
    failure_kind='transient_api' if any(x in error_text for x in ['429','502','503','504','connection reset','timed out']) else None
    record={'run':work.name,'attempt':attempt.name,'session_id':state.get('session_id'),'resume':False,
            'enable_tools':enable_tools,'skills':'episode-production','model':MODEL,'backend':'codex-chatgpt-cli',
            'timeout_seconds':timeout,'seconds':result['seconds'],'exit':result['exit_code'],'produced':produced,
            'expected_output':expected_output,'expected_output_produced':expected_output in produced,
            'refusal':recovery.refusal_reason(final),'summary_only':recovery.summary_ending(final),
            'turn_limit_reached':False,'failure_kind':failure_kind,'usage':result['usage'],
            'context_rotation':{'previous_session_id':old.get('session_id'),'reason':'Native Codex Luna with current task and preserved evidence'}}
    state['last_attempt']=attempt.name;write(state_path,state);write(attempt/'attempt.json',record)
    # Native events are retained without pretending to be Claude SDK events.
    shutil.copy2(attempt/'events.jsonl',attempt/'session.jsonl')
    return Attempt(run=work.name,directory=attempt,exit_code=result['exit_code'],seconds=round(result['seconds']),produced=produced,kept=max(0,len(before)-len(produced)),record=record)

def install():
    from novel_manga.application.agents.episode import session,turns
    session.run_session=run_session;turns.run_session=run_session
