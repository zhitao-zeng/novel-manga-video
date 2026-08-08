#!/usr/bin/env python3
"""Render a validated Qwen plan from admitted, content-matched visual assets.

The media provider is deliberately configured with fail-closed commands. If a
reuse cache entry is missing or invalid, the run stops instead of calling an
external image or video service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

from novel_manga.config import Settings
from novel_manga.ingest import read_novel
from novel_manga.models import (
    EpisodePlan,
    EpisodeStatus,
    StoryBible,
    SubmissionManifest,
    VideoRecord,
)
from novel_manga.multivoice import load_multivoice_script
from novel_manga.production import SeriesAssetFactory, compile_production_plan, sha256_file
from novel_manga.production_models import SeriesAssetManifest
from novel_manga.production_runtime import EpisodeProductionRuntime
from novel_manga.providers.command import CommandMediaProvider
from novel_manga.render import Renderer
from novel_manga.runtime_backends import RuntimeEvidenceBackends
from novel_manga.util import atomic_write_json, media_duration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--reuse-manifest", type=Path, required=True)
    parser.add_argument("--qwen-cache", type=Path, required=True)
    parser.add_argument("--sensevoice-model-dir", type=Path, required=True)
    parser.add_argument("--asr-python", type=Path, required=True)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    project = Path(__file__).resolve().parents[1]
    novel_dir = args.novel_dir.resolve()
    reuse_path = args.reuse_manifest.resolve()
    asr_python = args.asr_python.resolve()
    if not asr_python.is_file():
        raise FileNotFoundError(asr_python)
    reuse = json.loads(reuse_path.read_text(encoding="utf-8"))
    source_episode_dir = Path(reuse["source_episode_dir"])
    source_novel_dir = Path(reuse["source_novel_dir"])
    video_id = str(reuse["video_id"])
    episode_dir = novel_dir / video_id
    episode_dir.mkdir(parents=True, exist_ok=True)

    novel = read_novel(args.source, novel_id=args.novel_id, title=args.title)
    if len(novel.episodes) != 1:
        raise ValueError("reuse renderer currently requires exactly one parsed episode")
    episode = novel.episodes[0]
    bible = StoryBible.model_validate_json(
        (novel_dir / "story_bible.json").read_text(encoding="utf-8")
    )
    plan = EpisodePlan.model_validate_json(
        (episode_dir / "episode_plan.json").read_text(encoding="utf-8")
    )
    qwen_script = load_multivoice_script(novel_dir / "qwen_multivoice_script.json")

    source_assets_dir = source_novel_dir / "series_assets"
    target_assets_dir = novel_dir / "series_assets"
    shutil.copytree(source_assets_dir, target_assets_dir, dirs_exist_ok=True)
    assets = SeriesAssetManifest.model_validate_json(
        (target_assets_dir / "manifest.json").read_text(encoding="utf-8")
    )
    role_speakers = {role: profile.speaker for role, profile in qwen_script.voices.items()}
    assets.voice_assignments["narrator"] = role_speakers["narrator"]
    for character in bible.characters:
        if character.name in role_speakers:
            assets.voice_assignments[character.name] = role_speakers[character.name]
    atomic_write_json(target_assets_dir / "manifest.json", assets.model_dump(mode="json"))

    cached_tts = project / "scripts" / "cached_tts_command.py"
    sensevoice = project / "scripts" / "sensevoice_asr_command.py"
    os.environ["NOVEL_QWEN_TTS_CACHE_DIR"] = str(args.qwen_cache.resolve())
    os.environ["NOVEL_SENSEVOICE_MODEL_DIR"] = str(args.sensevoice_model_dir.resolve())
    settings = Settings(
        provider="command",
        admission_mode="production",
        output_root=novel_dir.parent,
        image_command="/bin/false",
        video_command="/bin/false",
        tts_model=f"{qwen_script.model}+validated-cache",
        tts_command=f"{sys.executable} {cached_tts}",
        voice_map=assets.voice_assignments,
        asr_command=f"{asr_python} {sensevoice}",
        media_workers=2,
        video_workers=2,
    )
    settings.validate()
    media = CommandMediaProvider(settings)
    renderer = Renderer(settings)
    asset_factory = SeriesAssetFactory(settings, media)
    evidence = RuntimeEvidenceBackends(settings)
    runtime = EpisodeProductionRuntime(settings, media, renderer, asset_factory, evidence)

    runtime_plan = compile_production_plan(video_id, episode, plan, bible, assets)
    atomic_write_json(
        episode_dir / "production_plan.json", runtime_plan.model_dump(mode="json")
    )
    for unit in runtime_plan.units:
        runtime._prepare_audio(episode_dir, unit)

    source_plan = json.loads(
        (source_episode_dir / "production_plan.json").read_text(encoding="utf-8")
    )
    source_units = {unit["unit_id"]: unit for unit in source_plan["units"]}
    matches = {item["target_unit_id"]: item for item in reuse["matches"]}
    seed_rows = []
    for unit in runtime_plan.units:
        match = matches[unit.unit_id]
        source_id = match["source_unit_id"]
        source_unit = source_units[source_id]
        source_video = source_episode_dir / source_unit["raw_video_path"]
        source_keyframe = source_episode_dir / source_unit["keyframe_path"]
        source_audio = source_episode_dir / source_unit["audio_path"]
        target_video = episode_dir / unit.raw_video_path
        target_keyframe = episode_dir / unit.keyframe_path
        target_audio = episode_dir / unit.audio_path
        for required in (source_video, source_keyframe, source_audio, target_audio):
            if not required.is_file():
                raise FileNotFoundError(required)
        if unit.speaking and _sha256(source_audio) != _sha256(target_audio):
            raise ValueError(f"{unit.unit_id} visible dialogue audio is not byte-identical")

        target_video.parent.mkdir(parents=True, exist_ok=True)
        target_keyframe.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_video, target_video)
        shutil.copy2(source_keyframe, target_keyframe)
        reference_board = asset_factory.reference_board(
            episode_dir, unit, assets, novel_dir
        )
        identity = runtime._visual_identity(unit, target_audio, reference_board)
        source_meta_path = source_video.with_suffix(source_video.suffix + ".request.json")
        source_meta = (
            json.loads(source_meta_path.read_text(encoding="utf-8"))
            if source_meta_path.is_file()
            else {}
        )
        target_meta = target_video.with_suffix(target_video.suffix + ".request.json")
        atomic_write_json(
            target_meta,
            {
                "request_sha256": identity,
                "attempt": int(source_meta.get("attempt", 1)),
                "video_sha256": sha256_file(target_video),
                "audio_sha256": sha256_file(target_audio),
                "keyframe_sha256": sha256_file(target_keyframe),
                "keyframe_prompt": unit.keyframe_prompt,
                "motion_prompt": unit.motion_prompt,
                "reference_board": str(reference_board.relative_to(episode_dir)),
                "reference_board_sha256": sha256_file(reference_board),
                "origin": "validated-content-addressed-reuse",
                "reuse_source_unit_id": source_id,
                "reuse_source_video": str(source_video),
                "reuse_source_video_sha256": sha256_file(source_video),
                "reuse_match_type": match["match_type"],
                "visible_audio_byte_identical": (
                    _sha256(source_audio) == _sha256(target_audio)
                    if unit.speaking
                    else None
                ),
            },
        )
        seed_rows.append(
            {
                "target_unit_id": unit.unit_id,
                "source_unit_id": source_id,
                "speaking": unit.speaking,
                "video_sha256": sha256_file(target_video),
                "keyframe_sha256": sha256_file(target_keyframe),
                "audio_sha256": sha256_file(target_audio),
            }
        )
    atomic_write_json(
        episode_dir / "visual_reuse_report.json",
        {
            "schema_version": 1,
            "unit_count": len(seed_rows),
            "visual_reuse_ratio": 1.0,
            "visible_audio_byte_identical_count": sum(
                bool(row["speaking"]) for row in seed_rows
            ),
            "units": seed_rows,
        },
    )

    final_video = episode_dir / f"{video_id}.mp4"
    cover = episode_dir / f"{video_id}_cover.jpeg"
    ending = episode_dir / f"{video_id}_ending.jpeg"
    qc = runtime.run(
        novel_dir=novel_dir,
        episode_dir=episode_dir,
        episode=episode,
        episode_plan=plan,
        bible=bible,
        series_assets=assets,
        final_video=final_video,
        cover=cover,
        ending=ending,
        video_id=video_id,
        episode_count=1,
    )
    record = VideoRecord(
        video_id=video_id,
        video_title=plan.video_title,
        video_cover=f"{video_id}/{cover.name}",
        ending_screen=f"{video_id}/{ending.name}",
        video_file=f"{video_id}/{final_video.name}",
        text_count=episode.text_count,
        status=EpisodeStatus.SUCCEEDED if qc["passed"] else EpisodeStatus.FAILED,
        error=None if qc["passed"] else "production admission failed",
    )
    manifest = SubmissionManifest(
        novel_id=args.novel_id,
        novel_title=args.title,
        video_count=1,
        videos=[record],
    )
    atomic_write_json(novel_dir / "manifest.json", manifest.model_dump(mode="json"))
    elapsed = time.monotonic() - started
    report = {
        "schema_version": 1,
        "controller": "model-neutral-production-controller",
        "tts": qwen_script.model,
        "external_image_calls": 0,
        "external_video_calls": 0,
        "visual_reuse_ratio": 1.0,
        "unit_count": len(runtime_plan.units),
        "visible_dialogue_units": sum(unit.speaking for unit in runtime_plan.units),
        "admission_passed": bool(qc["passed"]),
        "video": str(final_video),
        "video_seconds": round(media_duration(final_video), 3),
        "elapsed_seconds": round(elapsed, 3),
    }
    atomic_write_json(novel_dir / "qwen_reuse_render_report.json", report)
    atomic_write_json(
        novel_dir / "说明文件.json",
        {
            "format_version": "2.0",
            "runtime_contract": "novel-manga-production/v1",
            "codex_required": False,
            "novel_id": args.novel_id,
            "novel_title": args.title,
            "video_count": 1,
            "planner_backend": "prevalidated-external-planner",
            "tts_backend": qwen_script.model,
            "visual_origin": "validated-content-addressed-reuse",
            "output_spec": {
                "width": 1080,
                "height": 1920,
                "fps": 25,
                "video": "H.264/AAC MP4",
                "images": "JPEG",
            },
            "generation_seconds": round(elapsed, 3),
            "manifest": "manifest.json",
        },
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if qc["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
