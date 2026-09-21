"""Run one agent in the sandbox container, from the command line.

The work is novel_manga.application.agents.sandbox; this is the command entry.  It used to be the
other way round, with everything in this file - and then nothing in src/ could start an agent, because
src/ may not import scripts/, so a planning flow that wanted one had no way to ask.

    python scripts/run_agent_thin.py --run cp100-merge --skills merge
    python scripts/run_agent_thin.py --run v3-drama-1 --skills drama --timeout 5400

The key comes from the process environment or the project .env, by the name the config gives, and is
handed to docker by name only: never printed, never a command-line argument, never written into the
run directory.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_manga.application.agents.sandbox import DEFAULT_CONFIG, SandboxRefused, load_config, run_agent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True, help="沙箱 runs/ 下的目录名，里面要有 input/ 和 prompt.txt")
    parser.add_argument("--skills", default="", help="用哪套已安装的技能（configs/agent_sandbox.json 的 skills 表）")
    parser.add_argument("--replace-skills", action="store_true",
                        help="运行目录里已装的技能和这次要的不一样时，换成这次要的（默认拒绝运行）")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--model", default="", help="覆盖配置里的模型")
    parser.add_argument("--timeout", type=int, default=0, help="容器内的秒数上限")
    parser.add_argument("--prompt", type=Path, help="默认用 <run>/prompt.txt")
    args = parser.parse_args()
    try:
        attempt = run_agent(args.run, skills=args.skills, config=load_config(args.config),
                            model=args.model, timeout=args.timeout, prompt=args.prompt,
                            replace_skills=args.replace_skills,
                            log=lambda line: print(line, flush=True))
    except SandboxRefused as refusal:
        print(str(refusal), file=sys.stderr)
        return 2
    return attempt.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
