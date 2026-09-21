"""Run one agent in the sandbox container, from the pipeline instead of by hand.

Two stages need an agent: the skills that write a storyboard, and the merge that decides who is who.
The repo already writes their inputs and reads their outputs - what was missing in between is the
part that actually starts the thing, so every run so far has been a person typing docker commands.

The container, the image and the installed skills are environment, like the model endpoints, and
stay outside the repo; where they live is configs/agent_sandbox.json.  The invocation belongs here,
because the pipeline is what decides a merge should run.

    python scripts/run_agent_thin.py --run cp100-merge --skills merge
    python scripts/run_agent_thin.py --run v3-drama-1 --skills drama --timeout 5400

The key comes from the process environment or the project .env, by the name the config gives, and is
handed to docker by name only: never printed, never a command-line argument, never written into the
run directory.

A run has a stable name; each execution of it is an attempt, and the two used to be the same thing.
Everything that needed to tell them apart then answered for itself, and the answers contradicted:
the skills directory was kept because it existed, the logs were emptied because they existed, the
old output files were counted because they existed, and the whole directory was deleted because it
existed.  So `--skills` could name a skill set that was not the one that ran, a docker that never
started still wiped the log that would have said why, and a run that wrote nothing still reported
the previous attempt's files as its own.  Now an attempt owns its logs, its record and its own list
of what it produced, and the skills installed in a run have to be the skills that were asked for.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

DEFAULT_CONFIG = ROOT / "configs" / "agent_sandbox.json"


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


def install_skills(run_dir: Path, template: Path, *, replace: bool) -> str | None:
    """Put the asked-for skills in the run, or refuse to run something else under their name."""
    installed = run_dir / ".claude"
    wanted = skills_digest(template)
    if installed.exists() and skills_digest(installed) != wanted:
        if not replace:
            print(f"这个运行目录里装的技能不是 {template.parent.name}（或者模板改过了）。"
                  f"要换就加 --replace-skills，要留着上次那套就换一个 --run 名字。", file=sys.stderr)
            return None
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
    raise RuntimeError(f"attempts/{name} 这一秒里已经有 99 次尝试了")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, help="沙箱 runs/ 下的目录名，里面要有 input/ 和 prompt.txt")
    parser.add_argument("--skills", help="用哪套已安装的技能（configs/agent_sandbox.json 的 skills 表）")
    parser.add_argument("--replace-skills", action="store_true",
                        help="运行目录里已装的技能和这次要的不一样时，换成这次要的（默认拒绝运行）")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--model", help="覆盖配置里的模型")
    parser.add_argument("--timeout", type=int, help="容器内的秒数上限")
    parser.add_argument("--prompt", type=Path, help="默认用 <run>/prompt.txt")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    run_dir = Path(config["runs_root"]) / args.run
    prompt_path = args.prompt or (run_dir / "prompt.txt")
    if not prompt_path.is_file():
        print(f"没有提示词：{prompt_path}", file=sys.stderr)
        return 2

    # The installed skills come from a run that already has them; without .claude the agent has no
    # method to follow and writes whatever it likes.
    digest = ""
    if args.skills:
        template = Path(config["runs_root"]) / config["skills"][args.skills] / ".claude"
        if not template.is_dir():
            print(f"技能模板不在：{template}", file=sys.stderr)
            return 2
        digest = install_skills(run_dir, template, replace=args.replace_skills)
        if digest is None:
            return 2
    output = run_dir / "output"
    output.mkdir(parents=True, exist_ok=True)
    (run_dir / ".agent-home").mkdir(exist_ok=True)

    key = key_for(config["key_var"])
    if not key:
        print(f"环境变量和 .env 里都没有 {config['key_var']}", file=sys.stderr)
        return 2
    model = args.model or config["model"]
    seconds = args.timeout or config.get("timeout_seconds", 5400)
    environment = {**os.environ, "ANTHROPIC_AUTH_TOKEN": key}

    command = [
        "docker", "run", "--rm", "--name", f"agent-{args.run}",
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
    attempt = unique_attempt(run_dir / "attempts",
                             f"{time.strftime('%Y%m%d-%H%M%S')}-{args.skills or 'none'}")
    print(f"[{time.strftime('%T')}] {args.run}: {model} @ {config['base_url']}，上限 {seconds}s"
          f"，本次 {attempt.name}", flush=True)
    with open(attempt / "session.jsonl", "w") as session, open(attempt / "stderr.log", "w") as errors:
        code = subprocess.call(command, stdout=session, stderr=errors, env=environment)
    elapsed = round(time.time() - started)

    # Only what this attempt wrote, decided by comparing against the snapshot taken before it ran.
    # Walking the whole output directory reported the previous attempt's files as this one's produce,
    # including for an attempt that wrote nothing at all; a timestamp cutoff would have kept doing it
    # whenever two attempts land inside one clock tick, which is exactly when it matters least to be
    # nearly right.
    files = [p for p in output.rglob("*") if p.is_file()]
    produced = sorted(str(p.relative_to(output)) for p in files
                      if before.get(p) != p.stat().st_mtime_ns)
    kept = len(files) - len(produced)
    record = {"run": args.run, "attempt": attempt.name, "skills": args.skills or "",
              "skills_digest": digest, "model": model, "base_url": config["base_url"],
              "image": config["image"], "timeout_seconds": seconds,
              "started": time.strftime("%F %T", time.localtime(started)), "seconds": elapsed,
              "exit": code, "produced": produced, "kept_from_earlier_attempts": kept}
    (attempt / "attempt.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    latest = run_dir / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(Path("attempts") / attempt.name)
    print(f"[{time.strftime('%T')}] {args.run}: exit={code}，{elapsed}s，本次产出 {len(produced)} 个文件"
          + (f"：{'、'.join(produced[:6])}" if produced else "")
          + (f"（output/ 里另有 {kept} 个是更早的尝试留下的）" if kept else ""), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
