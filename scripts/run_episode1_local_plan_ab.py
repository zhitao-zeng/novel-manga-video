#!/usr/bin/env python3
"""Generate one auditable first-episode screenplay with the local planner.

This is the A arm of the episode-1 screenplay comparison.  It performs no
media generation and writes only to the research evidence directory.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from novel_manga.config import Settings
from novel_manga.ingest import read_novel
from novel_manga.models import StoryBible
from novel_manga.planner import OpenAICompatiblePlanner
from novel_manga.util import atomic_write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--story-bible", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18012/v1")
    parser.add_argument("--model", default="Qwen3.8-27B")
    parser.add_argument("--max-revisions", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    novel = read_novel(
        args.source.resolve(),
        novel_id="ftj-anime-api10-local-qwen38-ab",
        title="焚天纪",
    )
    if len(novel.episodes) != 1:
        raise ValueError("episode-1 A/B expects exactly one source chapter")
    bible = StoryBible.model_validate_json(
        args.story_bible.resolve().read_text(encoding="utf-8")
    )
    settings = Settings(
        provider="mock",
        admission_mode="preview",
        output_root=output,
        planner_backend="openai-compatible",
        llm_base_url=args.base_url,
        llm_api_key="local-not-needed",
        llm_model=args.model,
        llm_max_tokens=25000,
        llm_disable_thinking=True,
        planner_max_revisions=args.max_revisions,
        bounded_review_fallback=True,
        creative_profile="short-drama-adaptive-v1",
        request_timeout=900.0,
    )
    settings.validate()
    config = {
        "backend": "local-openai-compatible",
        "base_url": args.base_url,
        "model": args.model,
        "creative_profile": settings.creative_profile,
        "planner_max_revisions": settings.planner_max_revisions,
        "source_file": str(args.source.resolve()),
        "source_title": novel.episodes[0].source_title,
    }
    atomic_write_json(output / "planner_config.json", config)
    started = time.monotonic()
    try:
        bundle = OpenAICompatiblePlanner(settings).plan_episode_bundle(
            novel,
            novel.episodes[0],
            bible,
        )
    except Exception as error:
        atomic_write_json(
            output / "failure.json",
            {
                **config,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "error_type": type(error).__name__,
                "error": str(error)[:12000],
            },
        )
        raise
    atomic_write_json(
        output / "chapter_diagnosis.json",
        bundle.diagnosis.model_dump(mode="json"),
    )
    atomic_write_json(
        output / "episode_plan.json",
        bundle.plan.model_dump(mode="json"),
    )
    atomic_write_json(
        output / "script_quality_report.json",
        bundle.quality_report.model_dump(mode="json"),
    )
    atomic_write_json(
        output / "updated_series_state.json",
        bundle.updated_series_state.model_dump(mode="json"),
    )
    atomic_write_json(
        output / "run_report.json",
        {
            **config,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "shot_count": len(bundle.plan.shots),
            "turn_count": sum(len(shot.turns) for shot in bundle.plan.shots),
            "quality_passed": bundle.quality_report.passed,
        },
    )
    print(json.dumps(json.loads((output / "run_report.json").read_text()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
