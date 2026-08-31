#!/usr/bin/env python3
"""Generate one auditable first-episode screenplay with the local planner.

This is the A arm of the episode-1 screenplay comparison.  It performs no
media generation and writes only to the research evidence directory.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path

from novel_manga.config import NATIVE_DIALOGUE_POLICY, Settings
from novel_manga.ingest import read_novel
from novel_manga.models import StoryBible
from novel_manga.planner import CommandPlanner
from novel_manga.util import atomic_write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--story-bible", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--novel-id", default="ftj-ep1-v5-deepseek-ab")
    parser.add_argument("--base-url", default="http://127.0.0.1:4000")
    parser.add_argument("--model", default="deepseek-local")
    parser.add_argument(
        "--planner-command",
        type=Path,
        default=Path("scripts/deepseek_local_planner_command.py"),
    )
    parser.add_argument("--max-revisions", type=int, default=2)
    return parser.parse_args()


def archive_failure(output: Path) -> None:
    failure = output / "failure.json"
    if not failure.is_file():
        return
    index = 1
    while (output / f"failure.attempt_{index:02d}.json").exists():
        index += 1
    failure.replace(output / f"failure.attempt_{index:02d}.json")


def main() -> int:
    args = parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive_failure(output)
    novel = read_novel(
        args.source.resolve(),
        novel_id=args.novel_id,
        title="焚天纪",
    )
    if len(novel.episodes) != 1:
        raise ValueError("episode-1 A/B expects exactly one source chapter")
    bible = StoryBible.model_validate_json(
        args.story_bible.resolve().read_text(encoding="utf-8")
    )
    planner_command = args.planner_command.resolve()
    if not planner_command.is_file():
        raise FileNotFoundError(planner_command)
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(planner_command))}"
    os.environ["DEEPSEEK_LOCAL_ROUTER_URL"] = args.base_url
    os.environ["DEEPSEEK_LOCAL_MODEL"] = args.model
    settings = Settings(
        provider="mock",
        admission_mode="preview",
        output_root=output,
        planner_backend="command",
        planner_command=command,
        planner_max_revisions=args.max_revisions,
        planner_beat_max_retries=1,
        planning_timeout_seconds=600.0,
        creative_profile="short-drama-adaptive-v1",
        final_audio_policy=NATIVE_DIALOGUE_POLICY,
        request_timeout=900.0,
    )
    settings.validate()
    config = {
        "backend": "local-command",
        "protocol": "anthropic-messages-via-local-router",
        "base_url": args.base_url,
        "model_alias": args.model,
        "resolved_upstream_model": (
            "DeepSeek-V4-Flash-0731"
            if args.model == "deepseek-local"
            else "not-verified-by-this-runner"
        ),
        "planner_command": command,
        "novel_id": args.novel_id,
        "creative_profile": settings.creative_profile,
        "planner_max_revisions": settings.planner_max_revisions,
        "planner_beat_max_retries": settings.planner_beat_max_retries,
        "planning_timeout_seconds": settings.planning_timeout_seconds,
        "final_audio_policy": settings.final_audio_policy,
        "source_file": str(args.source.resolve()),
        "source_title": novel.episodes[0].source_title,
    }
    atomic_write_json(output / "planner_config.json", config)
    started = time.monotonic()
    try:
        bundle = CommandPlanner(settings).plan_episode_bundle(
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
    if bundle.episode_contract is not None:
        atomic_write_json(
            output / "episode_contract.json",
            bundle.episode_contract.model_dump(mode="json"),
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
