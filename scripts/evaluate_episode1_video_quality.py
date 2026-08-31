#!/usr/bin/env python3
"""Run clip-level and director/VLM video gates for episode 1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from novel_manga.production_models import ProductionPlan
from novel_manga.video_quality import evaluate_generated_video_quality


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--director-review", type=Path)
    parser.add_argument("--allow-missing-director-review", action="store_true")
    args = parser.parse_args()
    episode_dir = args.episode_dir.resolve()
    manifest = json.loads(
        (episode_dir / "sd25_direct_plan.json").read_text(encoding="utf-8")
    )
    plan = ProductionPlan.model_validate_json(
        (episode_dir / "production_plan_sd25.json").read_text(encoding="utf-8")
    )
    director_review = (
        json.loads(args.director_review.resolve().read_text(encoding="utf-8"))
        if args.director_review is not None and args.director_review.is_file()
        else None
    )
    report = evaluate_generated_video_quality(
        episode_dir=episode_dir,
        manifest=manifest,
        plan=plan,
        director_review=director_review,
        report_path=episode_dir / "episode_video_quality_report.json",
        require_director_review=not args.allow_missing_director_review,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
