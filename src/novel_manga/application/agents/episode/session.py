"""An episode's persistent Claude workspace; rendering and acceptance stay on the host."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from novel_manga.util import read_json

from novel_manga.application.agents.sandbox import (
    Attempt,
    SandboxRefused,
    key_for,
    load_config,
    unique_attempt,
    provider_container_args,
)

SKILL_SOURCE = Path(__file__).resolve().parents[5] / "skills" / "episode-production"
SESSION_FILE = "episode-session.json"
FILE_TOOLS = "Read,Write,Edit,Glob,Grep"


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _snapshot(output: Path, destination: Path) -> None:
    shutil.copytree(output, destination)


def run_session(work_dir: Path | str, prompt_text: str, *, resume: bool = False,
                timeout: int = 1200, config: dict | None = None,
                expected_output: str | None = None, enable_tools: bool = False,
                project_root: Path | None = None) -> Attempt:
    """Write one draft or repair in the same episode conversation.

    The first call creates an explicit session id; later calls must use ``resume=True``.
    ``produced`` lists files changed by this invocation, relative to ``output/``.
    A resumed draft correction can set ``expected_output="episode.json"``.
    With ``enable_tools=True``, Bash can invoke the workspace's host workflow client;
    the same episode agent then runs checks, generation, review and repair itself.
    The host still owns media services, budgets, caching and independent acceptance.
    """
    config = load_config() if config is None else config
    work_dir = Path(work_dir).resolve()
    if not prompt_text.strip():
        raise SandboxRefused("The episode session needs a prompt.")
    if timeout <= 0:
        raise SandboxRefused("The episode session timeout must be positive.")
    expected_output = expected_output or ("repair.json" if resume and not enable_tools else "episode.json")
    if expected_output not in {"episode.json", "requests.json", "repair.json"}:
        raise SandboxRefused("Expected output must be episode.json, requests.json or repair.json.")
    # The running code owns its skill. project_root supplies runtime settings,
    # not an older skill that happens to remain in a production checkout.
    skill_source = SKILL_SOURCE
    key = key_for(config["key_var"])
    if not key:
        raise SandboxRefused(f"No credential in {config['key_var']} or the project .env.")

    state_path = work_dir / SESSION_FILE
    if resume:
        if not state_path.is_file():
            raise SandboxRefused("No saved episode session to resume.")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        session_id = state.get("session_id", "")
        try:
            uuid.UUID(session_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise SandboxRefused("The saved episode session id is missing or invalid.") from exc
        session_log = work_dir / ".agent-home/.claude/projects/-work" / f"{session_id}.jsonl"
        if not session_log.is_file():
            raise SandboxRefused("The saved Claude transcript is missing; this session cannot resume.")
    else:
        if state_path.exists():
            raise SandboxRefused("This episode already has a session; use resume=True.")
        session_id = str(uuid.uuid4())
        state = {"session_id": session_id}

    context_rotation=None
    if enable_tools and (work_dir/'input/current-task.json').is_file():
        if resume and state.get('current_task_policy')!=1:
            archive=work_dir/'episode-session-before-current-task.json'
            _write_json(archive,state)
            context_rotation={'previous_session_id':session_id,'archive':archive.name,
                'reason':'Start current-task context without replaying obsolete production conversations'}
            session_id=str(uuid.uuid4())
            state={'session_id':session_id}
            resume=False
        state['current_task_policy']=1

    skills_dir = work_dir / ".claude" / "skills"
    if skills_dir.exists() and any(p.name != "episode-production" for p in skills_dir.iterdir()):
        raise SandboxRefused("This workspace contains other skills; use a dedicated episode workspace.")
    skill_dir = skills_dir / "episode-production"
    if not skill_dir.exists():
        shutil.copytree(skill_source, skill_dir)
    output = work_dir / "output"
    output.mkdir(parents=True, exist_ok=True)
    (work_dir / ".agent-home").mkdir(exist_ok=True)
    _write_json(state_path, state)

    attempt = unique_attempt(work_dir / "attempts", f"{time.strftime('%Y%m%d-%H%M%S')}-episode")
    if enable_tools:
        shutil.copytree(skill_dir, attempt / "skill-before")
        shutil.copytree(skill_source, skill_dir, dirs_exist_ok=True)
        shutil.copytree(skill_dir, attempt / "skill-current")
    (attempt / "prompt.txt").write_text(prompt_text, encoding="utf-8")
    before = {p.relative_to(output): (p.stat().st_mtime_ns, p.stat().st_size)
              for p in output.rglob("*") if p.is_file()}
    _snapshot(output, attempt / "output-before")
    model = config["model"]
    environment = {**os.environ, "ANTHROPIC_AUTH_TOKEN": key}
    command = [
        "docker", "run", "--rm", "--name", f"episode-agent-{session_id}",
        "--user", "1001:1001", "-e", "HOME=/work/.agent-home",
        "-v", f"{work_dir}:/work", "-w", "/work",
        "-e", f"ANTHROPIC_BASE_URL={config['base_url']}", "-e", "ANTHROPIC_AUTH_TOKEN",
        *provider_container_args(config),
    ]
    if enable_tools:
        command.extend(['-v',f'{skill_source.parents[1]/"src"}:/opt/novel-code:ro'])
    for name in ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                 "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
        command.extend(["-e", f"{name}={model}"])
    if config.get('max_thinking_tokens') is not None:
        command.extend(['-e', f"MAX_THINKING_TOKENS={int(config['max_thinking_tokens'])}",
                        '-e', 'CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1'])
    direct_draft = ((work_dir/'output/episode.json').is_file()
                    and read_json(work_dir.parent/'source-draft-call.json', {}).get('status') == 'candidate')
    role = ('Repair the existing production draft using the reported check results and original source. '
            'Consult only the episode-production skill sections needed for the current problem; '
            'the host already supplies fixed H3 syntax, bindings and timing.' if direct_draft else
            'Read /work/.claude/skills/episode-production/SKILL.md '
            'and its output contract before authoring this episode.')
    if read_json(work_dir/'input/manifest.json',{}).get('production_mode') in {'pipeline_review','batch_review'}:
        role = ('Repair the confirmed input defect or review the existing film, as specified by the current phase. '
                'Reuse completed production stages. '
                'Consult only relevant episode-production skill sections and shared repair cases; '
                'do not create a replacement screenplay or repeat the production pipeline.')
    command.extend([
        "-e", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1",
        "-e", f"CLAUDE_CODE_EFFORT_LEVEL={config.get('effort', 'medium')}",
        config["image"], "timeout", str(timeout), "claude", "-p", prompt_text,
        "--resume" if resume else "--session-id", session_id,
        "--model", model,
        "--dangerously-skip-permissions", "--tools", FILE_TOOLS + (",Bash" if enable_tools else ""),
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--setting-sources", "", "--output-format", "stream-json", "--verbose",
        "--append-system-prompt", role,
    ])
    if config.get('max_turns') is not None:
        turns = int(config['max_turns'])
        if turns <= 0:
            raise ValueError('max_turns must be positive')
        command.extend(['--max-turns', str(turns)])
    started = time.time()
    with (attempt / "session.jsonl").open("w", encoding="utf-8") as session, \
            (attempt / "stderr.log").open("w", encoding="utf-8") as errors:
        try:
            code = subprocess.call(command, stdout=session, stderr=errors, env=environment)
        except OSError as exc:
            errors.write(str(exc) + "\n")
            code = 127
    elapsed = round(time.time() - started)
    files = [p for p in output.rglob("*") if p.is_file()]
    produced = sorted(
        str(p.relative_to(output)) for p in files
        if before.get(p.relative_to(output)) != (p.stat().st_mtime_ns, p.stat().st_size)
        or (attempt / "output-before" / p.relative_to(output)).read_bytes() != p.read_bytes()
    )
    _snapshot(output, attempt / "output-after")
    completion = {}
    last_text = ''
    with (attempt / 'session.jsonl').open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'result':
                completion = event
            elif event.get('type') == 'assistant':
                content = (event.get('message') or {}).get('content') or []
                if isinstance(content, list):
                    text = '\n'.join(str(p.get('text') or '') for p in content if p.get('type') == 'text')
                    if text:
                        last_text = text
    from .recovery import refusal_reason, summary_ending
    record = {
        "run": work_dir.name, "attempt": attempt.name, "session_id": session_id, "resume": resume,
        "enable_tools": enable_tools,
        "skills": "episode-production", "model": model, "image": config["image"],
        "timeout_seconds": timeout, "seconds": elapsed, "exit": code, "produced": produced,
        "kept_from_earlier_attempts": len(files) - len(produced),
        "expected_output": expected_output, "expected_output_produced": expected_output in produced,
        'turn_limit_reached': completion.get('subtype') == 'error_max_turns',
        'num_turns': completion.get('num_turns'),
        'refusal': refusal_reason(completion.get('result') or last_text),
        'summary_only': summary_ending(completion.get('result') or last_text),
        **({'context_rotation':context_rotation} if context_rotation else {}),
    }
    _write_json(attempt / "attempt.json", record)
    state["last_attempt"] = attempt.name
    _write_json(state_path, state)
    return Attempt(run=work_dir.name, directory=attempt, exit_code=code, seconds=elapsed,
                   produced=produced, kept=len(files) - len(produced), record=record)
