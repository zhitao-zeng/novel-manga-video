"""Run one agent in the sandbox container, from the pipeline instead of by hand.

Two stages need an agent: the skills that write a storyboard, and the merge that decides who is who.
The repo already writes their inputs and reads their outputs - what was missing in between is the
part that actually starts the thing, so every run so far has been a person typing docker commands.

The container, the image and the installed skills are environment, like the model endpoints, and
stay outside the repo; where they live is configs/agent_sandbox.json.  The invocation belongs here,
because the pipeline is what decides a merge should run.

    python scripts/run_agent_thin.py --run cp100-merge --skills merge
    python scripts/run_agent_thin.py --run v3-drama-1 --skills drama --timeout 5400

The key is read from .env by the name the config gives and handed to docker by name only: it is
never printed, never a command-line argument, and never written into the run directory.
"""
from __future__ import annotations

import argparse
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


def key_from_env_file(name: str) -> str:
    """Read one variable out of the project .env, the way the sandbox script does."""
    path = ROOT / ".env"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, help="沙箱 runs/ 下的目录名，里面要有 input/ 和 prompt.txt")
    parser.add_argument("--skills", help="用哪套已安装的技能（configs/agent_sandbox.json 的 skills 表）")
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
    if args.skills:
        template = Path(config["runs_root"]) / config["skills"][args.skills] / ".claude"
        if not (run_dir / ".claude").exists():
            if not template.is_dir():
                print(f"技能模板不在：{template}", file=sys.stderr)
                return 2
            shutil.copytree(template, run_dir / ".claude")
    (run_dir / "output").mkdir(parents=True, exist_ok=True)
    (run_dir / ".agent-home").mkdir(exist_ok=True)

    key = key_from_env_file(config["key_var"])
    if not key:
        print(f".env 里没有 {config['key_var']}", file=sys.stderr)
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
    started = time.time()
    print(f"[{time.strftime('%T')}] {args.run}: {model} @ {config['base_url']}，上限 {seconds}s", flush=True)
    with open(run_dir / "session.jsonl", "w") as session, open(run_dir / "stderr.log", "w") as errors:
        code = subprocess.call(command, stdout=session, stderr=errors, env=environment)
    elapsed = round(time.time() - started)
    with open(run_dir / "stderr.log", "a") as errors:
        errors.write(f"exit={code} seconds={elapsed} model={model} base={config['base_url']}\n")
    produced = sorted(p.name for p in (run_dir / "output").rglob("*") if p.is_file())
    print(f"[{time.strftime('%T')}] {args.run}: exit={code}，{elapsed}s，产出 {len(produced)} 个文件"
          + (f"：{'、'.join(produced[:6])}" if produced else ""), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
