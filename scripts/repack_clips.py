#!/usr/bin/env python
"""Re-pack planned episodes to a different clip length, without planning anything again.

An episode's expensive part - reading the chapter, writing the shots and the dialogue - lands
in chapter_script.json and never mentions a clip length.  Clip length only enters when those
shots are packed into clips, which build_clip_plan_thin.py does locally from environment:
NOVEL_CLIP_SECONDS_MAX=15 caps a clip at 15 s and a clip at three stages instead of six.

So converting a 30 s novel to 15 s costs no model calls at all.  It re-runs the packer.

    repack_clips.py <novel> [--seconds 15] [--apply] [--include-rendered] [--workers N]

Rendered episodes are left alone by default: re-packing renumbers the clips and rewrites the
prompts, so every clip already paid for would be orphaned and the episode would re-render from
nothing.  The old plan is kept beside the new one as clip_plan.<n>s.json.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKER = ROOT / "scripts" / "build_clip_plan_thin.py"


def plan_length(episode: Path) -> float | None:
    try:
        plan = json.loads((episode / "clip_plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return float((plan.get("limits") or {}).get("max_clip_seconds") or 0) or None


def rendered_clips(episode: Path) -> int:
    clips = episode / "work" / "clips"
    return len(list(clips.glob("*/attempt_01/clip.mp4"))) if clips.is_dir() else 0


def repack(episode: Path, bible: Path, seconds: int, tier: str) -> tuple[str, dict]:
    """Run the packer for one episode with the new cap, keeping the old plan beside it."""
    old = json.loads((episode / "clip_plan.json").read_text(encoding="utf-8"))
    was = int((old.get("limits") or {}).get("max_clip_seconds") or 0)
    for name in ("clip_plan.json", "clip_plan.md"):
        source = episode / name
        if source.is_file():
            keep = episode / name.replace(".", f".{was}s.", 1)
            if not keep.exists():
                shutil.copy2(source, keep)
    env = {**os.environ, "NOVEL_CLIP_SECONDS_MAX": str(seconds)}
    result = subprocess.run([sys.executable, str(PACKER), "--episode-dir", str(episode),
                             "--bible", str(bible), "--tier", tier],
                            capture_output=True, text=True, env=env)
    if result.returncode != 0:
        return "failed", {"why": (result.stderr or result.stdout).strip()[-200:]}
    new = json.loads((episode / "clip_plan.json").read_text(encoding="utf-8"))
    over = [c for c in new["clips"] if c.get("kind") == "video" and (c.get("request_seconds") or 0) > seconds]
    return "ok", {
        "clips_before": len([c for c in old["clips"] if c.get("kind") == "video"]),
        "clips_after": len([c for c in new["clips"] if c.get("kind") == "video"]),
        "seconds_before": old.get("totals", {}).get("estimated_seconds"),
        "seconds_after": new.get("totals", {}).get("estimated_seconds"),
        "over_cap": len(over),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("novel")
    parser.add_argument("--seconds", type=int, default=15, choices=(15, 30))
    parser.add_argument("--apply", action="store_true", help="不加就只看会动哪些集")
    parser.add_argument("--force", action="store_true",
                        help="长度已经对也重排：用来吸收音色库、辨识特征这类打包期的新东西")
    parser.add_argument("--include-rendered", action="store_true",
                        help="连已经渲过的集也重排——它们的成片会全部作废，要重渲")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--tier", default="fast", choices=("fast", "quality"))
    parser.add_argument("--chapters", help="只处理这些集，如 1-578 或 5,9,20-30")
    args = parser.parse_args()

    novel_dir = ROOT / "outputs" / args.novel
    bible = novel_dir / "story_bible.json"
    if not bible.is_file():
        raise SystemExit(f"没有 {bible}")
    wanted = None
    if args.chapters:
        wanted = set()
        for part in args.chapters.split(","):
            if "-" in part:
                a, b = part.split("-", 1)
                wanted.update(range(int(a), int(b) + 1))
            else:
                wanted.add(int(part))

    todo, skipped = [], Counter()
    for episode in sorted(novel_dir.glob(f"{args.novel}_*")):
        index = episode.name.rsplit("_", 1)[-1]
        if not index.isdigit() or not (episode / "clip_plan.json").is_file():
            continue
        if wanted is not None and int(index) not in wanted:
            continue
        if not (episode / "chapter_script.json").is_file():
            skipped["没有分镜稿"] += 1
            continue
        length = plan_length(episode)
        if length == args.seconds and not args.force:
            skipped[f"已经是 {args.seconds} 秒"] += 1
            continue
        made = rendered_clips(episode)
        if made and not args.include_rendered:
            skipped["已渲过，跳过"] += 1
            continue
        todo.append(episode)

    print(f"{args.novel}: 要重排 {len(todo)} 集 → {args.seconds} 秒档")
    for why, n in skipped.most_common():
        print(f"  跳过 {n} 集：{why}")
    if not todo:
        return 0
    if not args.apply:
        print(f"\n（预览。加 --apply 才动手；旧计划会留成 clip_plan.30s.json）")
        print("前 10 集：", ", ".join(e.name for e in todo[:10]))
        return 0

    done, failed = [], []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for episode, (status, info) in zip(todo, pool.map(lambda e: repack(e, bible, args.seconds, args.tier), todo)):
            if status == "ok":
                done.append(info)
                if len(done) % 100 == 0:
                    print(f"  {len(done)}/{len(todo)}", flush=True)
            else:
                failed.append((episode.name, info.get("why", "")))
    before = sum(d["clips_before"] for d in done)
    after = sum(d["clips_after"] for d in done)
    over = sum(d["over_cap"] for d in done)
    print(f"\n重排 {len(done)} 集，失败 {len(failed)}")
    print(f"  片段数 {before} → {after}（每集 {before/max(1,len(done)):.1f} → {after/max(1,len(done)):.1f}）")
    print(f"  超过 {args.seconds} 秒的片段：{over}")
    for name, why in failed[:5]:
        print(f"  失败 {name}: {why[:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
