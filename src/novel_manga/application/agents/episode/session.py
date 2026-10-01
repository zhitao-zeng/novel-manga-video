"""An episode's persistent Claude workspace; rendering and acceptance stay on the host."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from novel_manga.application.agents.sandbox import (
    Attempt,
    SandboxRefused,
    key_for,
    load_config,
    unique_attempt,
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
    supplied = Path(project_root) / 'skills/episode-production' if project_root is not None else None
    skill_source = supplied if supplied is not None and supplied.is_dir() else SKILL_SOURCE
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
    ]
    for name in ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                 "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
        command.extend(["-e", f"{name}={model}"])
    command.extend([
        "-e", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1",
        "-e", f"CLAUDE_CODE_EFFORT_LEVEL={config.get('effort', 'medium')}",
        config["image"], "timeout", str(timeout), "claude", "-p", prompt_text,
        "--resume" if resume else "--session-id", session_id,
        "--dangerously-skip-permissions", "--tools", FILE_TOOLS + (",Bash" if enable_tools else ""),
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--setting-sources", "", "--output-format", "stream-json", "--verbose",
        "--append-system-prompt", "Read /work/.claude/skills/episode-production/SKILL.md "
        "and its output contract before authoring this episode.",
    ])
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
    record = {
        "run": work_dir.name, "attempt": attempt.name, "session_id": session_id, "resume": resume,
        "enable_tools": enable_tools,
        "skills": "episode-production", "model": model, "image": config["image"],
        "timeout_seconds": timeout, "seconds": elapsed, "exit": code, "produced": produced,
        "kept_from_earlier_attempts": len(files) - len(produced),
        "expected_output": expected_output, "expected_output_produced": expected_output in produced,
    }
    _write_json(attempt / "attempt.json", record)
    state["last_attempt"] = attempt.name
    _write_json(state_path, state)
    return Attempt(run=work_dir.name, directory=attempt, exit_code=code, seconds=elapsed,
                   produced=produced, kept=len(files) - len(produced), record=record)
