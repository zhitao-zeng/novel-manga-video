"""Explicit Codex-login Luna runner for the existing bounded proposal/review sandbox API."""
import shutil
import sys
import time
from pathlib import Path

from luna_transport import MODEL, CODEX, run as execute, write


def run_agent(run, *, skills='', config=None, model='', timeout=0, prompt=None,
              replace_skills=False, log=print):
    from novel_manga.application.agents import sandbox
    from novel_manga.llm import usage
    config = config or sandbox.load_config()
    work = Path(config['runs_root'])/run
    prompt_path = Path(prompt) if prompt else work/'prompt.txt'
    if not prompt_path.is_file():
        raise sandbox.SandboxRefused(f'Missing sandbox prompt: {prompt_path}')
    digest = ''
    if skills:
        if skills not in (config.get('skills') or {}):
            raise sandbox.SandboxRefused(f'Unknown sandbox skill: {skills}')
        template = Path(config['runs_root'])/config['skills'][skills]/'.claude'
        if not template.is_dir():
            raise sandbox.SandboxRefused(f'Missing sandbox skill template: {template}')
        digest = sandbox.install_skills(work, template, replace=replace_skills)
    output = work/'output'; output.mkdir(exist_ok=True)
    before = {str(p.relative_to(output)): (p.stat().st_mtime_ns, p.stat().st_size)
              for p in output.rglob('*') if p.is_file()}
    attempt = sandbox.unique_attempt(work/'attempts', time.strftime('%Y%m%d-%H%M%S')+'-codex-luna')
    for p in output.rglob('*'):
        if p.is_file():
            saved = attempt/'output-before'/p.relative_to(output)
            saved.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p, saved)
    seconds = timeout or config.get('timeout_seconds', 600)
    instructions = (
        f'You are the explicitly selected Codex Luna production agent. Your evidence workspace is {work}. '
        'Read the copied evidence and the supplied skill as requested below. Write only the requested output files '
        'under output/. Preserve input, tools, history and existing attempts. Do not invoke model or video-generation '
        'services or modify production directories. Read actual JPEG images with your image viewing tool; do not '
        'treat written reviews as visual facts. Legacy Read/Bash/Write tool names describe operations: use your '
        'available file/image/shell tools. A legacy /work/ prefix means this evidence workspace. '
        'Complete the requested decision/review files and then finish immediately.\n\n'
        + prompt_path.read_text().replace('/work/', str(work)+'/'))
    command = [CODEX, 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check',
               '--sandbox', 'workspace-write', '-m', MODEL,
               '-c', 'model_reasoning_effort='+config.get('effort', 'medium'),
               '--json', '--output-last-message', str(attempt/'answer.txt'), '-']
    log(f'{run}: {MODEL} via current Codex login, limit {seconds}s, attempt {attempt.name}')
    result = execute(command, attempt, prompt=instructions, timeout=seconds, cwd=work)
    code = result['exit_code'] if result['completed'] else result['exit_code'] or 1
    produced = [str(p.relative_to(output)) for p in output.rglob('*') if p.is_file()
                and before.get(str(p.relative_to(output))) != (p.stat().st_mtime_ns, p.stat().st_size)]
    record = {'run': run, 'attempt': attempt.name, 'model': MODEL, 'backend': 'codex-chatgpt-cli',
              'requested_model': model or config.get('model'), 'skills': skills, 'skills_digest': digest,
              'timeout_seconds': seconds, 'seconds': result['seconds'], 'exit': code,
              'produced': produced, 'usage': result['usage'], 'thread_id': result.get('thread_id')}
    write(attempt/'attempt.json', record)
    shutil.copy2(attempt/'events.jsonl', attempt/'session.jsonl')
    latest = work/'latest'
    if latest.is_symlink() or latest.exists(): latest.unlink()
    latest.symlink_to(Path('attempts')/attempt.name)
    usage.record(name='bounded-sandbox', model=MODEL,
                 usage={**result['usage'], 'cache_read_input_tokens': result['usage'].get('cached_input_tokens', 0)},
                 url='codex://chatgpt-cli')
    log(f'{run}: exit={code}, produced {len(produced)} current files')
    return sandbox.Attempt(run=run, directory=attempt, exit_code=code, seconds=round(result['seconds']),
                           produced=produced, kept=max(0, len(before)-len(produced)), record=record)


def install():
    from novel_manga.application.agents import sandbox
    if sandbox.run_agent is run_agent:
        return
    old_run, old_config = sandbox.run_agent, sandbox.load_config
    def load_config(path=None):
        return {**old_config(path), 'model': MODEL, 'backend': 'codex-chatgpt-cli'}
    for name, module in list(sys.modules.items()):
        if name.startswith('novel_manga.'):
            if getattr(module, 'run_agent', None) is old_run: module.run_agent = run_agent
            if getattr(module, 'load_config', None) is old_config: module.load_config = load_config
