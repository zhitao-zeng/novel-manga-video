"""A range of chapters through the sandbox: storyboard, accept what needs no choosing, bind, check.

    python scripts/agent_storyboard_batch_thin.py --novel-dir outputs/<书> --chapters 1-300

Safe to stop and start again: a chapter that is accepted and bound is not asked for twice, and one
that is waiting for a person stays waiting rather than costing another quarter of an hour.  Where
every chapter stands is in <书>/agent_storyboard_batch.md, rewritten as each chapter lands, with the
ones that need somebody at the top.

How many run at once comes from configs/agent_sandbox.json and the hour - two by day, four on the
night shift - and is asked again whenever a slot frees up, because a book takes longer than a shift.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from novel_manga.application.agents import storyboard as agent_storyboard  # noqa: E402
from novel_manga.application.agents import storyboard_batch as batch  # noqa: E402
from novel_manga.application.agents.sandbox import key_for, load_config  # noqa: E402
from novel_manga.application.production.common import parse_chapters  # noqa: E402

# The local H3 renders fifteen seconds at most, and the planner sizes clips for a paid model twice that
# long unless it is told.  It is told here, by the thing that starts the planner, because the night it
# was left to whoever wrote the launch script, ninety-nine chapters were planned that nothing could
# render.  The plan is then read back and checked: a setting that did not take is a failed binding.
LOCAL_H3 = {"NOVEL_CLIP_SECONDS_MAX": "15", "NOVEL_LOCAL_H3_URL": "pool",
            "NOVEL_VIDEO_MODEL": "minimax-h3-ref2va-turbo"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--chapters", required=True, help='例如 "1-300" 或 "1,3,5-7"')
    parser.add_argument("--skill", default="", help="默认用 profile.agent_skill")
    parser.add_argument("--repropose", action="store_true",
                        help="对还在等人选稿的章节再写一版（默认不写：再写一版也还是要人选）")
    parser.add_argument("--keep-video-env", action="store_true",
                        help="绑定时不套本地 H3 的 15 秒规格，用当前环境里的视频模型设置")
    args = parser.parse_args()

    novel_dir = args.novel_dir.resolve()
    profile_path = novel_dir / "profile.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8")) if profile_path.is_file() else {}
    skill = args.skill or str(profile.get("agent_skill") or "")
    if not skill:
        print("没有技能可用：给 --skill，或在 profile.json 里写 agent_skill", file=sys.stderr)
        return 2
    if str(profile.get("planning_backend", "local")) != "sandbox_agent":
        print("这本书的 profile.planning_backend 不是 sandbox_agent：分镜写出来、采用了，规划也不会去绑它。"
              "先把 profile 改过来。", file=sys.stderr)
        return 2
    config = load_config()
    environment = dict(os.environ) if args.keep_video_env else {**os.environ, **LOCAL_H3}
    cap = None if args.keep_video_env else float(LOCAL_H3["NOVEL_CLIP_SECONDS_MAX"])

    def propose(chapter: int):
        return agent_storyboard.propose(novel_dir, batch.episode_dir(novel_dir, chapter), chapter, skill,
                                        config=config, log=lambda line: print(line, flush=True))

    def bind(chapter: int) -> tuple[bool, str]:
        directory = batch.episode_dir(novel_dir, chapter)
        subprocess.run([sys.executable, str(ROOT / "scripts" / "thin_batch.py"), "--novel-dir", str(novel_dir),
                        "--chapters", str(chapter), "--stage", "plan", "--replan",
                        "--no-eager-cards", "--no-recurring-cards"],
                       cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # The batch prints a table and exits 0 whatever happened to the chapter, so the plan is what
        # says whether there is one.
        if not batch.bound_since_accepted(directory):
            failure = directory / "planning_failed.json"
            errors = json.loads(failure.read_text(encoding="utf-8")).get("errors", []) if failure.is_file() else []
            return False, (" | ".join(errors)[:200] or f"没有排出计划，看 {directory / 'plan.log'}")
        limits = json.loads((directory / "clip_plan.json").read_text(encoding="utf-8")).get("limits") or {}
        if cap is not None and float(limits.get("max_clip_seconds") or 0) != cap:
            return False, f"计划的单段上限是 {limits.get('max_clip_seconds')} 秒，不是 {cap:g} 秒，本地 H3 渲不了"
        return True, ""

    def trace(chapter: int) -> tuple[bool, str]:
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / "authored_trace_thin.py"),
                               "--novel-dir", str(novel_dir), "--chapter", str(chapter)],
                              cwd=ROOT, env=environment, capture_output=True, text=True)
        lines = [line.strip() for line in (done.stdout + done.stderr).splitlines() if line.strip()]
        return done.returncode == 0, ("" if done.returncode == 0 else " / ".join(lines[-3:])[:300])

    def endpoint_up() -> bool:
        try:
            answer = httpx.get(config["base_url"].rstrip("/") + "/v1/models", timeout=10, trust_env=False,
                               headers={"Authorization": f"Bearer {key_for(config['key_var'])}"})
        except httpx.HTTPError:
            return False
        return answer.status_code < 500

    steps = batch.Steps(propose=propose, bind=bind, trace=trace, endpoint_up=endpoint_up,
                        busy=batch.default_busy(novel_dir.name, skill),
                        log=lambda line: print(line, flush=True))
    chapters = parse_chapters(args.chapters)
    print(f"{novel_dir.name}: {len(chapters)} 章，技能 {skill}，此刻最多同时 {batch.parallel_now(config)} 个", flush=True)
    results = batch.run_batch(novel_dir, chapters, steps, config=config, repropose=args.repropose)
    counts = {name: sum(1 for r in results if r.outcome == key) for key, name in batch.HEADINGS.items()}
    print("结束：" + "，".join(f"{name} {n}" for name, n in counts.items())
          + f"。报告：{novel_dir / (batch.REPORT + '.md')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
