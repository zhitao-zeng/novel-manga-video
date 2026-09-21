"""Run one agent in the sandbox container, as a service the pipeline can call.

This used to live entirely in scripts/run_agent_thin.py, which made it unreachable: `src/` may not
import `scripts/` (tests/test_architecture_boundaries.py), so a planning flow that wanted an agent
had no way to start one and a person had to type the command.  The script is now the command entry
and this is the thing it runs, so the same invocation serves a person and the pipeline.

The container, the image and the installed skills are environment, like the model endpoints, and
stay outside the repo; where they live is configs/agent_sandbox.json.

A run has a stable name; each execution of it is an attempt, and the two used to be the same thing.
Everything that needed to tell them apart then answered for itself, and the answers contradicted:
the skills directory was kept because it existed, the logs were emptied because they existed, the
old output files were counted because they existed, and the whole directory was deleted because it
existed.  An attempt now owns its logs, its record and its own list of what it produced.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from novel_manga.application.configuration import project_root

ROOT = project_root()
DEFAULT_CONFIG = ROOT / "configs" / "agent_sandbox.json"


class SandboxRefused(ValueError):
    """The run cannot start as asked - a missing prompt, an unknown skill, no credential."""


@dataclass
class Attempt:
    """One execution of a run: where its record is, and what it actually produced."""
    run: str
    directory: Path
    exit_code: int
    seconds: int
    produced: list[str] = field(default_factory=list)
    kept: int = 0
    record: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """The process finished cleanly.  Whether what it wrote is usable is a separate question, and
        the caller has to ask it: exit=0 does not mean there is a storyboard worth binding."""
        return self.exit_code == 0


def load_config(path: Path | None = None) -> dict:
    return json.loads(Path(path or DEFAULT_CONFIG).read_text(encoding="utf-8"))


def key_for(name: str) -> str:
    """The credential, from the process environment first and the project .env second.

    Reading only .env meant a container or a scheduler that injects credentials the normal way - an
    exported variable - was told the key was missing while it sat in the environment.
    """
    value = str(os.environ.get(name) or "").strip()
    if value:
        return value
    path = ROOT / ".env"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def skills_digest(directory: Path) -> str:
    """What the skills in a directory ARE, so "the same skills" stops meaning "a directory exists"."""
    parts = []
    for file in sorted(p for p in directory.rglob("*") if p.is_file()):
        parts.append(f"{file.relative_to(directory)}:{hashlib.sha256(file.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def install_skills(run_dir: Path, template: Path, *, replace: bool) -> str:
    """Put the asked-for skills in the run, or refuse to run something else under their name."""
    installed = run_dir / ".claude"
    wanted = skills_digest(template)
    if installed.exists() and skills_digest(installed) != wanted:
        if not replace:
            raise SandboxRefused(
                f"这个运行目录里装的技能不是 {template.parent.name}（或者模板改过了）。"
                f"要换就加 --replace-skills，要留着上次那套就换一个 --run 名字。")
        shutil.rmtree(installed)
    if not installed.exists():
        shutil.copytree(template, installed)
    return wanted


def unique_attempt(attempts: Path, name: str) -> Path:
    """A directory no attempt has had before.  The stamp is to the second, and two attempts inside one
    second sharing a directory is the very thing an attempt id exists to prevent."""
    attempts.mkdir(parents=True, exist_ok=True)
    for suffix in ("", *(f"-{n}" for n in range(2, 100))):
        attempt = attempts / (name + suffix)
        try:
            attempt.mkdir()
            return attempt
        except FileExistsError:
            continue
    raise SandboxRefused(f"attempts/{name} 这一秒里已经有 99 次尝试了")


def container_running(name: str) -> bool:
    """Whether a container by this name is up right now, so nothing deletes a live run's files."""
    try:
        result = subprocess.run(["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return name in result.stdout.split()


def run_agent(run: str, *, skills: str = "", config: dict | None = None, model: str = "",
              timeout: int = 0, prompt: Path | None = None, replace_skills: bool = False,
              log=print) -> Attempt:
    """Start the container, wait for it, and return what this attempt did.

    Raises SandboxRefused before touching anything when the run cannot start as asked.
    """
    config = config or load_config()
    run_dir = Path(config["runs_root"]) / run
    prompt_path = Path(prompt) if prompt else (run_dir / "prompt.txt")
    if not prompt_path.is_file():
        raise SandboxRefused(f"没有提示词：{prompt_path}")

    # The installed skills come from a run that already has them; without .claude the agent has no
    # method to follow and writes whatever it likes.
    digest = ""
    if skills:
        if skills not in (config.get("skills") or {}):
            raise SandboxRefused(f"配置里没有这套技能：{skills}（有的是 {sorted(config.get('skills') or {})}）")
        template = Path(config["runs_root"]) / config["skills"][skills] / ".claude"
        if not template.is_dir():
            raise SandboxRefused(f"技能模板不在：{template}")
        digest = install_skills(run_dir, template, replace=replace_skills)
    output = run_dir / "output"
    output.mkdir(parents=True, exist_ok=True)
    (run_dir / ".agent-home").mkdir(exist_ok=True)

    key = key_for(config["key_var"])
    if not key:
        raise SandboxRefused(f"环境变量和 .env 里都没有 {config['key_var']}")
    model = model or config["model"]
    seconds = timeout or config.get("timeout_seconds", 5400)
    environment = {**os.environ, "ANTHROPIC_AUTH_TOKEN": key}

    command = [
        "docker", "run", "--rm", "--name", f"agent-{run}",
        "--user", "1001:1001", "-e", "HOME=/work/.agent-home",
        "-v", f"{run_dir}:/work", "-w", "/work",
        "-e", f"ANTHROPIC_BASE_URL={config['base_url']}", "-e", "ANTHROPIC_AUTH_TOKEN",
        *sum((["-e", f"{name}={model}"] for name in (
            "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL")), []),
        "-e", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1",
        "-e", f"CLAUDE_CODE_EFFORT_LEVEL={config.get('effort', 'medium')}",
        config["image"], "timeout", str(seconds), "claude", "-p",
        prompt_path.read_text(encoding="utf-8"),
        "--dangerously-skip-permissions", *config.get("extra_args", []),
        "--output-format", "stream-json", "--verbose",
    ]
    # This attempt's own directory, made now and never reused: a docker that fails to start (125) has
    # nothing of anyone else's to truncate, and its stderr is the only thing that can say why.
    started = time.time()
    before = {p: p.stat().st_mtime_ns for p in output.rglob("*") if p.is_file()}
    attempt = unique_attempt(run_dir / "attempts", f"{time.strftime('%Y%m%d-%H%M%S')}-{skills or 'none'}")
    log(f"[{time.strftime('%T')}] {run}: {model} @ {config['base_url']}，上限 {seconds}s，本次 {attempt.name}")
    with open(attempt / "session.jsonl", "w") as session, open(attempt / "stderr.log", "w") as errors:
        code = subprocess.call(command, stdout=session, stderr=errors, env=environment)
    elapsed = round(time.time() - started)

    # Only what this attempt wrote, decided against the snapshot taken before it ran.  Walking the whole
    # output directory reported the previous attempt's files as this one's produce, including for an
    # attempt that wrote nothing at all; a timestamp cutoff would keep doing it whenever two attempts
    # land inside one clock tick, which is exactly when being nearly right is worth least.
    files = [p for p in output.rglob("*") if p.is_file()]
    produced = sorted(str(p.relative_to(output)) for p in files if before.get(p) != p.stat().st_mtime_ns)
    kept = len(files) - len(produced)
    record = {"run": run, "attempt": attempt.name, "skills": skills, "skills_digest": digest,
              "model": model, "base_url": config["base_url"], "image": config["image"],
              "timeout_seconds": seconds, "started": time.strftime("%F %T", time.localtime(started)),
              "seconds": elapsed, "exit": code, "produced": produced,
              "kept_from_earlier_attempts": kept}
    (attempt / "attempt.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    latest = run_dir / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(Path("attempts") / attempt.name)
    log(f"[{time.strftime('%T')}] {run}: exit={code}，{elapsed}s，本次产出 {len(produced)} 个文件"
        + (f"：{'、'.join(produced[:6])}" if produced else "")
        + (f"（output/ 里另有 {kept} 个是更早的尝试留下的）" if kept else ""))
    return Attempt(run=run, directory=attempt, exit_code=code, seconds=elapsed,
                   produced=produced, kept=kept, record=record)
