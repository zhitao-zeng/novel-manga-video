"""Ask the sandbox for a chapter's storyboard, see what came back, and choose one.

The three steps a sandbox-planned chapter goes through, without anyone typing docker commands or
copying file paths into --bind-storyboard:

    # 1. ask for one
    python scripts/agent_storyboard_thin.py --novel-dir outputs/<书> --chapter 3 --propose

    # 2. see what came back
    python scripts/agent_storyboard_thin.py --novel-dir outputs/<书> --chapter 3

    # 3. choose the take; planning binds it from here on
    python scripts/agent_storyboard_thin.py --novel-dir outputs/<书> --chapter 3 \\
        --accept output/分镜表.xlsx [--sheet 第3集]

The skill comes from profile.agent_skill unless --skill overrides it, so the book's own setting is
what runs by default rather than whatever was typed last.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_manga.application.agents import storyboard as agent_storyboard
from novel_manga.application.agents.sandbox import SandboxRefused, load_config


def episode_dir(novel_dir: Path, chapter: int) -> Path:
    directory = novel_dir / f"{novel_dir.name}_{chapter}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--skill", default="", help="默认用 profile.agent_skill")
    parser.add_argument("--propose", action="store_true", help="跑一次沙箱，产出候选分镜")
    parser.add_argument("--accept", default="", help="采用这一份（相对运行目录，或绝对路径）")
    parser.add_argument("--sheet", default="", help="工作簿里用哪个工作表")
    parser.add_argument("--timeout", type=int, default=0)
    args = parser.parse_args()

    novel_dir = args.novel_dir.resolve()
    directory = episode_dir(novel_dir, args.chapter)
    profile_path = novel_dir / "profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.is_file() else {}
    skill = args.skill or str(profile.get("agent_skill") or "")

    try:
        if args.propose:
            if not skill:
                print("没有技能可用：给 --skill，或在 profile.json 里写 agent_skill", file=sys.stderr)
                return 2
            agent_storyboard.propose(novel_dir, directory, args.chapter, skill, timeout=args.timeout)
        elif args.accept:
            agent_storyboard.accept(directory, args.accept, sheet_name=args.sheet)
    except SandboxRefused as refusal:
        print(str(refusal), file=sys.stderr)
        return 2

    current = agent_storyboard.state(directory)
    runs_root = Path(load_config()["runs_root"])
    print(f"第 {args.chapter} 章：{current.status}"
          + (f"｜技能 {current.skill}｜运行 {current.run}｜尝试 {current.attempt}" if current.run else ""))
    if current.status == "candidate":
        if current.sheets:
            print("候选分镜：")
            for sheet in current.sheets:
                print(f"  {sheet}   （{runs_root / current.run / sheet}）")
            print("选一版：--accept <上面的相对路径> [--sheet 工作表名]")
        else:
            print("这次尝试没有产出分镜表。看 "
                  f"{runs_root / current.run / 'attempts' / current.attempt / 'stderr.log'}")
    elif current.status == "accepted":
        print(f"已采用：{current.sheet}" + (f"（工作表 {current.sheet_name}）" if current.sheet_name else "")
              + f"｜{current.accepted_at}")
        print("接下来照常规划这一章即可，绑定会自动用这一份。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
