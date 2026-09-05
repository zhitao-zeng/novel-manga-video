#!/usr/bin/env python
"""What a novel cost: paid video seconds, paid image cards, local model calls.

    cost_report_thin.py --novel-dir outputs/zhutian-fast [--chapters 11-70] [--csv out.csv]
    cost_report_thin.py --all                      # every novel under outputs/

Counts what was actually billed rather than what was planned:

* video - every Seedance attempt recorded in ``thin_media_report.json``, including
  the attempts that were rejected by a gate, because those were generated and
  paid for too.  Resolution comes from the episode's tier (fast = 480p).
* images - one ``*.task.json`` sidecar is written per accepted image generation,
  so counting sidecars under ``series_assets`` counts paid card renders, redraws
  and moderation retries included.
* local models - planner attempts (one Qwen call each) and VLM judgements.  These
  run on our own GPUs, so they are reported as calls, not money.

Rates live in ``configs/pricing.json`` (copy the template, fill in your prices).
Without rates the report still gives the units, which is what a price list needs.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRICING = REPO / "configs" / "pricing.json"
TIER_RESOLUTION = {"fast": "480p", "quality": "720p"}


def load_rates() -> dict:
    if PRICING.is_file():
        return json.loads(PRICING.read_text(encoding="utf-8"))
    return {"currency": "", "video_per_second": {}, "image_per_card": 0.0}


def episode_rows(novel_dir: Path, chapters: set[int] | None) -> list[dict]:
    """One row per rendered episode: paid seconds, attempts, planner calls."""
    profile = {}
    profile_path = novel_dir / "profile.json"
    if profile_path.is_file():
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    resolution = TIER_RESOLUTION.get(str(profile.get("tier", "quality")), "720p")

    rows = []
    for directory in sorted(d for d in novel_dir.iterdir() if d.is_dir() and d.name.startswith(novel_dir.name + "_")):
        try:
            index = int(directory.name.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            continue
        if chapters is not None and index not in chapters:
            continue
        report_path = directory / "thin_media_report.json"
        if not report_path.is_file():
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        attempts = [attempt for clip in report["clips"] for attempt in clip["attempts"]]
        seconds = sum(float(attempt.get("duration") or 0.0) for attempt in attempts)
        # The report is JSON, so ``selected`` is a copy of one of the attempts,
        # not the same object; compare on the clip file it points at.
        wasted = 0.0
        for clip in report["clips"]:
            kept = str((clip.get("selected") or {}).get("video") or "")
            wasted += sum(float(a.get("duration") or 0.0) for a in clip["attempts"] if str(a.get("video") or "") != kept)
        rows.append({
            "episode": directory.name,
            "chapter": index,
            "resolution": resolution,
            "video_seconds": round(seconds, 1),
            "discarded_seconds": round(wasted, 1),
            "clips": len(report["clips"]),
            "attempts": len(attempts),
            "planner_calls": len(list(directory.glob("request_attempt_*.json"))),
            "judge_clips": len(json.loads((directory / "episode_review.json").read_text(encoding="utf-8")).get("clips", []))
            if (directory / "episode_review.json").is_file() else 0,
            "final_seconds": round(float((report.get("assembly") or {}).get("duration") or 0.0), 1),
            "status": report.get("status", ""),
        })
    return rows


def image_calls(novel_dir: Path) -> tuple[int, str]:
    """Paid image generations, and where they were charged.

    A novel that reuses another novel's cards has ``series_assets`` symlinked;
    those renders were paid for once, under the novel that owns the directory.
    """
    assets = novel_dir / "series_assets"
    if not assets.exists():
        return 0, ""
    if assets.is_symlink():
        return 0, assets.resolve().parent.name
    return sum(1 for _ in assets.rglob("*.task.json")), ""


def money(rates: dict, resolution: str, seconds: float, images: int) -> float | None:
    per_second = (rates.get("video_per_second") or {}).get(resolution)
    per_image = rates.get("image_per_card")
    if not per_second and not per_image:
        return None
    return round(seconds * float(per_second or 0.0) + images * float(per_image or 0.0), 2)


def report(novel_dir: Path, chapters: set[int] | None, rates: dict, csv_path: Path | None) -> dict:
    rows = episode_rows(novel_dir, chapters)
    images, shared_with = image_calls(novel_dir)
    if not rows:
        print(f"{novel_dir.name}: 没有已出片的集")
        return {}
    seconds = sum(row["video_seconds"] for row in rows)
    discarded = sum(row["discarded_seconds"] for row in rows)
    resolution = rows[0]["resolution"]
    total = money(rates, resolution, seconds, images)
    currency = rates.get("currency", "")

    print(f"\n=== {novel_dir.name} ===")
    print(f"  出片 {len(rows)} 集，成片合计 {sum(r['final_seconds'] for r in rows) / 60:.1f} 分钟")
    print(f"  视频生成 {seconds:.0f} 秒 @{resolution}（其中被弃用的重试 {discarded:.0f} 秒），平均每集 {seconds / len(rows):.0f} 秒")
    print(f"  图片生成 {images} 次（角色卡、地点卡、重画和审核重试都算）" if not shared_with
          else f"  图片生成 0 次：卡片共享自 {shared_with}，已计在那本书下")
    print(f"  本地模型：规划 {sum(r['planner_calls'] for r in rows)} 次调用，审片 {sum(r['judge_clips'] for r in rows)} 段")
    if total is not None:
        print(f"  按 configs/pricing.json 的单价：{total} {currency}，每集 {total / len(rows):.2f} {currency}")
    else:
        print("  单价未填（configs/pricing.json），只报用量")
    worst = sorted(rows, key=lambda row: row["video_seconds"], reverse=True)[:3]
    print("  最贵的集：" + "，".join(f"{r['episode'].rsplit('_', 1)[1]}章 {r['video_seconds']:.0f}s" for r in worst))

    if csv_path:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"  明细写入 {csv_path}")
    return {"novel": novel_dir.name, "episodes": len(rows), "video_seconds": round(seconds, 1),
            "discarded_seconds": round(discarded, 1), "images": images, "resolution": resolution, "cost": total}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path)
    parser.add_argument("--all", action="store_true", help="every novel under outputs/ that has rendered episodes")
    parser.add_argument("--chapters", help="e.g. 11-70 or 11,12,13")
    parser.add_argument("--csv", type=Path, help="write the per-episode detail here")
    parser.add_argument("--json", action="store_true", help="print the totals as one JSON line")
    args = parser.parse_args()

    chapters = None
    if args.chapters:
        chapters = set()
        for piece in args.chapters.split(","):
            if "-" in piece:
                start, stop = piece.split("-")
                chapters.update(range(int(start), int(stop) + 1))
            elif piece.strip():
                chapters.add(int(piece))

    rates = load_rates()
    targets = []
    if args.all:
        root = REPO / "outputs"
        targets = [d for d in sorted(root.iterdir()) if d.is_dir() and any(d.glob(f"{d.name}_*/thin_media_report.json"))]
    elif args.novel_dir:
        targets = [args.novel_dir.resolve()]
    else:
        parser.error("pass --novel-dir or --all")

    totals = [report(directory, chapters, rates, args.csv if len(targets) == 1 else None) for directory in targets]
    totals = [row for row in totals if row]
    if len(totals) > 1:
        seconds = sum(row["video_seconds"] for row in totals)
        images = sum(row["images"] for row in totals)
        costs = [row["cost"] for row in totals if row["cost"] is not None]
        print(f"\n=== 合计 ===\n  {sum(row['episodes'] for row in totals)} 集，视频 {seconds / 60:.1f} 分钟，图片 {images} 次"
              + (f"，费用 {round(sum(costs), 2)} {rates.get('currency', '')}" if costs else ""))
    if args.json:
        print(json.dumps(totals, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
