#!/usr/bin/env python3
"""Render the approved 3D episode-1 recut with the offline command backend."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path

from novel_manga.config import Settings
from novel_manga.ingest import read_novel
from novel_manga.models import EpisodePlan, StoryBible
from novel_manga.production import SeriesAssetFactory
from novel_manga.production_models import ProductionPlan, SeriesAssetManifest
import novel_manga.production_runtime as runtime_module
from novel_manga.production_runtime import EpisodeProductionRuntime
from novel_manga.providers.command import CommandMediaProvider
from novel_manga.render import Renderer
from novel_manga.runtime_backends import RuntimeEvidenceBackends
from novel_manga.util import atomic_write_json


SILENT = "【无对白动作镜】"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    return parser.parse_args()


class UserRecutRuntime(EpisodeProductionRuntime):
    """Adds explicit silent visual units without changing the production Core."""

    def _prepare_audio(self, episode_dir, unit):  # type: ignore[override]
        if unit.text != SILENT:
            return super()._prepare_audio(episode_dir, unit)
        output = self._resolve(episode_dir, unit.audio_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.is_file():
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=48000:cl=mono",
                    "-t",
                    "4.0",
                    "-c:a",
                    "pcm_s16le",
                    str(output),
                ],
                check=True,
            )
        unit.audio_seconds = 4.0
        unit.speech_start = 0.0
        unit.speech_end = 4.0
        unit.subtitle_alignment = "silent_fixed_4s"
        return (
            {
                "unit_id": unit.unit_id,
                "reference": "",
                "hypothesis": "",
                "cer": 0.0,
                "status": "passed",
                "backend": "not-applicable-silent-visual-unit",
            },
            {
                "unit_id": unit.unit_id,
                "backend": "fixed-silence-v1",
                "evidence": "silent_visual_unit",
                "speech_start": 0.0,
                "speech_end": 4.0,
                "events": [],
            },
        )

    def _audit_delivered_asr(  # type: ignore[override]
        self,
        episode_dir: Path,
        final_video: Path,
        plan: ProductionPlan,
        delivery_timeline: list[dict],
    ) -> dict:
        spoken_units = [unit for unit in plan.units if unit.text != SILENT]
        spoken_ids = {unit.unit_id for unit in spoken_units}
        spoken_plan = plan.model_copy(update={"units": spoken_units})
        spoken_timeline = [
            row for row in delivery_timeline if str(row["unit_id"]) in spoken_ids
        ]
        report = super()._audit_delivered_asr(
            episode_dir,
            final_video,
            spoken_plan,
            spoken_timeline,
        )
        report["silent_visual_units_excluded"] = len(plan.units) - len(spoken_units)
        return report


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    novel_dir = args.novel_dir.resolve()
    episode_dir = novel_dir / args.video_id
    source = args.source.resolve()
    os.environ["NOVEL_LOCAL_IMAGE_STYLE_PROFILE"] = "3d-donghua"

    settings = Settings.from_env(
        provider="command",
        output_root=novel_dir.parent,
        admission_mode="production",
    )
    settings = replace(
        settings,
        intro_seconds=0.0,
        outro_seconds=4.0,
        final_audio_policy="locked_tts",
        local_visual_strategy="keyframe",
        creative_profile="short-drama-adaptive-v1",
        media_workers=1,
        video_workers=1,
    )
    settings.validate()
    novel = read_novel(
        source,
        novel_id="ftj-anime-api10-3d-script-ab",
        title="焚天纪",
    )
    if len(novel.episodes) != 1:
        raise ValueError("3D user recut requires exactly one chapter")
    bible = StoryBible.model_validate_json(
        (novel_dir / "story_bible.json").read_text(encoding="utf-8")
    )
    plan = EpisodePlan.model_validate_json(
        (episode_dir / "episode_plan.json").read_text(encoding="utf-8")
    )
    assets = SeriesAssetManifest.model_validate_json(
        (novel_dir / "series_assets" / "manifest.json").read_text(encoding="utf-8")
    )
    if assets.style_fingerprint != bible.style_fingerprint:
        raise ValueError("3D story bible and asset manifest fingerprint mismatch")

    media = CommandMediaProvider(settings)
    renderer = Renderer(settings)
    factory = SeriesAssetFactory(settings, media)
    evidence = RuntimeEvidenceBackends(settings)
    runtime = UserRecutRuntime(settings, media, renderer, factory, evidence)

    original_groups = runtime_module.build_visual_groups
    original_admission = runtime_module.evaluate_episode_admission

    def no_cross_shot_merge(*positional, **keywords):
        keywords["allow_cross_shot_merge"] = False
        return original_groups(*positional, **keywords)

    def silent_aware_admission(*, plan, **keywords):
        spoken_plan = plan.model_copy(
            update={"units": [unit for unit in plan.units if unit.text != SILENT]}
        )
        return original_admission(plan=spoken_plan, **keywords)

    runtime_module.build_visual_groups = no_cross_shot_merge
    runtime_module.evaluate_episode_admission = silent_aware_admission
    try:
        admission = runtime.run(
            novel_dir=novel_dir,
            episode_dir=episode_dir,
            episode=novel.episodes[0],
            episode_plan=plan,
            bible=bible,
            series_assets=assets,
            final_video=episode_dir / f"{args.video_id}.mp4",
            cover=episode_dir / f"{args.video_id}_cover.jpeg",
            ending=episode_dir / f"{args.video_id}_ending.jpeg",
            video_id=args.video_id,
            episode_count=1,
        )
    finally:
        runtime_module.build_visual_groups = original_groups
        runtime_module.evaluate_episode_admission = original_admission

    trace_path = episode_dir / "content_trace.json"
    if trace_path.is_file():
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        for row in trace.get("turns", []):
            if row.get("text") == SILENT:
                row["text"] = ""
                row["silent_visual_unit"] = True
                row["subtitle_alignment"] = "silent_fixed_4s"
        atomic_write_json(trace_path, trace)
    report = {
        "backend": "offline-command",
        "image_style_profile": "3d-donghua",
        "image_model": settings.image_model,
        "video_model": settings.video_model,
        "tts_model": settings.tts_model,
        "style_fingerprint": bible.style_fingerprint,
        "silent_visual_unit_policy": "fixed 4s silent reference audio; excluded from subtitles and ASR",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "admission_passed": bool(admission.get("passed")),
        "submission_eligible": bool(admission.get("submission_eligible")),
    }
    atomic_write_json(episode_dir / "local_run_report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if admission.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
