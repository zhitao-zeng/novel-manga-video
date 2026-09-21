"""Take a new novel from its text to a bible the planner can work from, in one run.

Every piece of this existed and was invoked by hand, and every hand-off was a decision made on the
spot - which is how a hundred-chapter reading of 87 people got handed to a single build call sized
for 12, how NOVEL_LLM_MAX_TOKENS got raised against a min() that ignores it, and how an agent ran on
a brief written before the rules it was supposed to follow.  None of those were model failures.  They
were choices that nothing had written down.

So the hand-offs live here now:

  1. 建工作区    build_bible_thin, no cast - the novel index and a seed bible have to exist before
                 anything can read the book by chapter.
  2. 逐章读      lean_bible_thin, parallel over the local Qwen instances, cached per chapter.
  3. 归并        the sandbox agent on Flash-Next, writing files: a book's cast does not fit one
                 reply, and the local 27B fails this (three hours, or 391k tokens against a 262k
                 window).  The brief is generated now, so it cannot be stale.
  4. 重建圣经    the reading names the cast; the build designs the ones the seed chapters show and
                 records the rest in reading_cast.json, which is what growth may add from later.

    python scripts/setup_book_thin.py inputs/X.md --novel-id x --title X --style meiman --chapters 1-100

Endpoints are checked before any of it runs: the failures they cause otherwise land after minutes of
work, on a stage that had nothing to do with them.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SANDBOX = ROOT / "configs" / "agent_sandbox.json"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def run(command: list[str], env: dict | None = None, name: str = "") -> int:
    log(f"→ {name or command[1]}")
    code = subprocess.call(command, env={**os.environ, **(env or {})}, cwd=ROOT)
    if code:
        log(f"✗ {name or command[1]} 退出码 {code}")
    return code


def alive(url: str, key: str = "") -> bool:
    request = urllib.request.Request(url.rstrip("/") + "/models")
    if key:
        request.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def env_value(name: str) -> str:
    path = ROOT / ".env"
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def container_running(name: str) -> bool:
    """Whether a container by this name is up right now, so a rerun cannot delete a live run's files."""
    try:
        result = subprocess.run(["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return name in result.stdout.split()


def preflight(config: dict, *, reading: bool) -> tuple[dict, list[str]]:
    """Every endpoint THIS run needs, checked before the first minute is spent on any of them.

    Only the ones it needs: --skip-read consumes a merge that already happened, and holding it back
    because the per-chapter extraction endpoints are down blocks a recovery on a service it will not
    call.  A check that fails for something the run never touches is noise standing in a doorway.
    """
    problems: list[str] = []
    overrides: dict[str, str] = {}

    if reading:
        extractors = [e.strip() for e in env_value("QWEN38_LOCAL_BASE_URL").split(",") if e.strip()]
        live = [e for e in extractors if alive(e)]
        if not live:
            problems.append("逐章提取没有可用端点：QWEN38_LOCAL_BASE_URL 里的实例都连不上")
        else:
            overrides["QWEN38_LOCAL_BASE_URL"] = ",".join(live)
            if len(live) < len(extractors):
                log(f"提取端点 {len(live)}/{len(extractors)} 可用，跳过死的：{set(extractors) - set(live)}")

    key = env_value(config["key_var"])
    agent_url = config["base_url"].rstrip("/") + "/v1"
    if not key:
        problems.append(f"归并要的密钥不在 .env：{config['key_var']}")
    elif reading and not alive(agent_url, key):
        problems.append(f"归并端点连不上：{config['base_url']}")

    bible_url = env_value("NOVEL_LLM_BASE_URL")
    bible_key = env_value("NOVEL_LLM_API_KEY")
    if bible_url and alive(bible_url, bible_key):
        log(f"建圣经用 .env 配的 {bible_url}")
    elif key and alive(agent_url, key):
        # Falling back silently would hide a dead endpoint; say which one and why.
        log(f"建圣经：.env 的 {bible_url or '(未设)'} 连不上，改用归并那台 {config['base_url']}")
        overrides.update({"NOVEL_LLM_BASE_URL": agent_url,
                          "NOVEL_LLM_MODEL": config["model"],
                          "NOVEL_LLM_API_KEY": key})
    else:
        problems.append("建圣经没有可用的 LLM 端点")
    return overrides, problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="小说原文（一个 .md/.txt，章节标题各占一行）")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--style", required=True)
    parser.add_argument("--frame", default="16:9")
    parser.add_argument("--chapters", default="1-100", help="读哪些章，例如 1-100")
    parser.add_argument("--bible-chapters", type=int, default=5, help="种子圣经从前几章设计")
    parser.add_argument("--output-root", default="outputs")
    parser.add_argument("--force", action="store_true", help="工作区已存在时重建")
    parser.add_argument("--skip-read", action="store_true", help="已经读过，直接用现成的归并结果")
    args = parser.parse_args()

    config = json.loads(SANDBOX.read_text(encoding="utf-8"))
    overrides, problems = preflight(config, reading=not args.skip_read)
    for problem in problems:
        log("✗ " + problem)
    if problems:
        log("开跑前的检查没过，先修这些再来——跑到一半才发现，前面的时间就白花了")
        return 2

    novel_dir = ROOT / args.output_root / args.novel_id
    run_name = f"{args.novel_id}-merge"
    export = Path(config["runs_root"]) / run_name / "output" / "export" / "story_bible.json"
    python = str(ROOT / ".venv" / "bin" / "python")

    # 1. 工作区：读书要按章切原文，得先有 novel.json 的章节表
    if not (novel_dir / "novel.json").is_file() or args.force:
        code = run([python, "scripts/build_bible_thin.py", args.source,
                    "--novel-id", args.novel_id, "--title", args.title, "--style", args.style,
                    "--frame", args.frame, "--output-root", args.output_root,
                    *(["--force"] if args.force else [])], overrides, "建工作区")
        if code:
            return code
    else:
        log(f"工作区已有：{novel_dir}")

    if not args.skip_read:
        # 2+3. 逐章读，再把归并交给沙箱里的 agent
        run_dir = Path(config["runs_root"]) / run_name
        if run_dir.exists():
            if container_running(f"agent-{run_name}"):
                log(f"✗ 归并容器还在跑：agent-{run_name}。先等它跑完或停掉，别删它正在写的目录")
                return 2
            # The inputs and the outputs are regenerated, so they go; attempts/ is the record of what
            # actually ran and with which skills, and deleting that is how a rerun erases the only
            # evidence of why the last one came out the way it did.
            log(f"清掉上一次的输入和产出（规则可能改过，输入必须重新生成），保留 attempts/：{run_dir}")
            for name in ("input", "output", "prompt.txt", ".claude"):
                target = run_dir / name
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
        code = run([python, "scripts/lean_bible_thin.py", "--novel-dir", str(novel_dir),
                    "--chapters", args.chapters, "--agent-input", str(run_dir)],
                   overrides, "逐章读 + 写归并输入")
        if code:
            return code
        code = run([python, "scripts/run_agent_thin.py", "--run", run_name,
                    "--skills", "merge", "--timeout", "7200"], overrides, "归并（沙箱 agent）")
        if code:
            return code

    if not export.is_file():
        log(f"✗ 归并没有产出 {export}")
        return 2

    # 4. 圣经：演员表由读书定，种子只设计前几章出场的人，其余进 reading_cast.json 交给 growth
    code = run([python, "scripts/build_bible_thin.py", args.source,
                "--novel-id", args.novel_id, "--title", args.title, "--style", args.style,
                "--frame", args.frame, "--output-root", args.output_root, "--force",
                "--bible-chapters", str(args.bible_chapters), "--cast", str(export)],
               overrides, "按读书的演员表建圣经")
    if code:
        return code

    bible = json.loads((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    roster = json.loads((novel_dir / "reading_cast.json").read_text(encoding="utf-8"))
    aliases = json.loads((novel_dir / "bible_aliases.json").read_text(encoding="utf-8"))
    log(f"完成：圣经 {len(bible['characters'])} 人 {len(bible['locations'])} 地点｜"
        f"读书名单 {len(roster['characters'])} 人｜别名 {len(aliases)} 条")
    log(f"下一步：{python} scripts/thin_batch.py --novel-dir {novel_dir} --chapters {args.chapters} --stage plan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
