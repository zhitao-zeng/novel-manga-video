#!/usr/bin/env python3
"""Render episode 1 with direct 3D assets and SD2.5, without keyframes."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from novel_manga.admission import evaluate_episode_admission
from novel_manga.config import Settings
from novel_manga.face_consistency import evaluate_face_consistency
from novel_manga.ingest import read_novel
from novel_manga.models import EpisodePlan, StoryBible
from novel_manga.production import SeriesAssetFactory
from novel_manga.production_models import ProductionPlan, SeriesAssetManifest
from novel_manga.production_runtime import EpisodeProductionRuntime, build_visual_groups
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.qc import inspect_media
from novel_manga.render import Renderer
from novel_manga.runtime_backends import RuntimeEvidenceBackends, aggregate_asr
from novel_manga.sd_dialogue import timed_subtitle_pages
from novel_manga.util import atomic_write_json, media_duration


SILENT = "【无对白动作镜】"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--skip-asr", action="store_true")
    return parser.parse_args()


def make_silence(path: Path, seconds: float = 4.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            "anullsrc=r=48000:cl=mono", "-t", f"{seconds:.3f}",
            "-c:a", "pcm_s16le", str(path),
        ],
        check=True,
    )


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    novel_dir = args.novel_dir.resolve()
    episode_dir = novel_dir / args.video_id
    direct_path = episode_dir / "sd25_direct_plan.json"
    direct = json.loads(direct_path.read_text(encoding="utf-8"))
    direct_by_id = {row["group_id"]: row for row in direct["groups"]}

    tts_cli = (
        "docker exec novel-ftj3-i2-it-h3-gpu1 /opt/venvs/controller/bin/python "
        "/app/runtime/local_model_cli.py tts"
    )
    bridge = Path(__file__).resolve().parent / "docker_audio_evidence_command.py"
    asr_cli = f"{os.sys.executable} {bridge} asr"
    align_cli = f"{os.sys.executable} {bridge} align"
    base = Settings.from_env(
        provider="phanrouter",
        output_root=novel_dir.parent,
        admission_mode="preview" if args.skip_asr else "production",
    )
    settings = replace(
        base,
        video_model="sd2.5",
        image_model="gpt-image-2",
        inline_reference_images=True,
        final_audio_policy="locked_tts",
        video_requires_audio=True,
        tts_model="IndexTTS-2.5",
        tts_command=tts_cli,
        asr_command=asr_cli,
        align_command=align_cli,
        media_workers=1,
        video_workers=2,
        intro_seconds=0.0,
        outro_seconds=4.0,
        poll_timeout=1800.0,
        request_timeout=300.0,
    )
    settings.validate()
    novel = read_novel(
        args.source.resolve(),
        novel_id="ftj-anime-api10-3d-script-ab",
        title="焚天纪",
    )
    episode = novel.episodes[0]
    bible = StoryBible.model_validate_json(
        (novel_dir / "story_bible.json").read_text(encoding="utf-8")
    )
    episode_plan = EpisodePlan.model_validate_json(
        (episode_dir / "episode_plan.json").read_text(encoding="utf-8")
    )
    assets = SeriesAssetManifest.model_validate_json(
        (novel_dir / "series_assets" / "manifest.json").read_text(encoding="utf-8")
    )
    plan = ProductionPlan.model_validate_json(
        (episode_dir / "production_plan_sd25.json").read_text(encoding="utf-8")
    )
    if not (
        plan.style_fingerprint == assets.style_fingerprint == bible.style_fingerprint
    ):
        raise ValueError("3D plan, bible, and asset fingerprints differ")

    provider = PhanRouterMediaProvider(settings)
    renderer = Renderer(settings)
    factory = SeriesAssetFactory(settings, provider)
    evidence = RuntimeEvidenceBackends(settings)
    runtime = EpisodeProductionRuntime(settings, provider, renderer, factory, evidence)
    role_voice = {
        "旁白": "mature_male",
        "narrator": "mature_male",
        "楚焱": "h3_chuyan_v1",
        "楚烟儿": "warm_female",
        "楚媚": "bright_female",
        "中年测验员": "deep_male",
    }
    for unit in plan.units:
        unit.voice = role_voice.get(unit.speaker_name, "mature_male")

    existing_alignment_path = episode_dir / "alignment_report.json"
    existing_alignments = (
        json.loads(existing_alignment_path.read_text(encoding="utf-8")).get("units", [])
        if existing_alignment_path.is_file()
        else []
    )
    existing_alignment_by_id = {
        str(row["unit_id"]): row for row in existing_alignments
    }
    asr_rows = []
    alignments = []
    for unit in plan.units:
        output = episode_dir / unit.audio_path
        if unit.text == SILENT:
            make_silence(output)
            unit.audio_seconds = 4.0
            unit.speech_start = 0.0
            unit.speech_end = 4.0
            unit.subtitle_alignment = "silent_fixed_4s"
            continue
        if args.skip_asr:
            if not output.is_file():
                provider.synthesize(
                    unit.text,
                    output,
                    voice=unit.voice,
                    instructions=f"标准普通话，准确朗读：{unit.text}",
                )
            seconds = media_duration(output)
            alignment = existing_alignment_by_id.get(unit.unit_id)
            if alignment is None:
                speech_start = 0.08
                speech_end = max(speech_start + 0.1, seconds - 0.12)
                alignment = {
                    "unit_id": unit.unit_id,
                    "backend": "coarse-audio-bounds-preview-v1",
                    "evidence": "audio_duration_character_paging",
                    "speech_start": speech_start,
                    "speech_end": speech_end,
                    "events": timed_subtitle_pages(
                        unit.text, speech_start, speech_end
                    ),
                }
            unit.audio_seconds = round(seconds, 6)
            unit.speech_start = float(alignment["speech_start"])
            unit.speech_end = float(alignment["speech_end"])
            unit.subtitle_alignment = str(alignment.get("evidence") or "coarse_audio_bounds")
            alignments.append(alignment)
            continue
        row, alignment = runtime._prepare_audio(episode_dir, unit)
        asr_rows.append(row)
        alignments.append(alignment)
    if args.skip_asr:
        prior_tts_report = episode_dir / "tts_asr_report.json"
        tts_report = (
            json.loads(prior_tts_report.read_text(encoding="utf-8"))
            if prior_tts_report.is_file()
            else {"status": "skipped", "turns": []}
        )
        tts_report["preview_reused_locked_audio_without_new_asr"] = True
        tts_report["audio_source"] = "locked_indextts_preview_asr_deferred"
    else:
        tts_report = aggregate_asr(asr_rows)
        tts_report["audio_source"] = "locked_indextts_reference_before_sd25"
    tts_report["silent_visual_units_excluded"] = sum(
        unit.text == SILENT for unit in plan.units
    )
    atomic_write_json(episode_dir / "tts_asr_report.json", tts_report)
    atomic_write_json(episode_dir / "alignment_report.json", {"units": alignments})

    plan.visual_groups = build_visual_groups(
        plan,
        series_assets=assets,
        target_seconds=13.4,
        allow_cross_shot_merge=False,
    )
    units = {unit.unit_id: unit for unit in plan.units}
    alignment_by_id = {str(row["unit_id"]): row for row in alignments}
    group_timelines: dict[str, list[dict]] = {}
    driver_by_group: dict[str, Path] = {}
    locked_by_group: dict[str, Path] = {}
    for group in plan.visual_groups:
        group_units = [units[unit_id] for unit_id in group.unit_ids]
        audios = [episode_dir / unit.audio_path for unit in group_units]
        locked, seconds, offsets, speed = renderer.compose_visual_group_audio(
            audios,
            episode_dir / group.audio_path,
            target_seconds=13.4,
        )
        driver, driver_seconds, driver_offsets, driver_speed = (
            renderer.compose_visual_group_audio(
                audios,
                episode_dir / group.video_audio_path,
                audible=[unit.speaking for unit in group_units],
                target_seconds=13.4,
            )
        )
        if abs(driver_seconds - seconds) > 0.03 or driver_offsets != offsets:
            raise RuntimeError(f"{group.group_id} driver timing drift")
        if abs(driver_speed - speed) > 1e-6:
            raise RuntimeError(f"{group.group_id} driver speed drift")
        group.audio_seconds = round(seconds, 6)
        group.speed_factor = round(speed, 8)
        locked_by_group[group.group_id] = locked
        driver_by_group[group.group_id] = driver
        timings = []
        for unit, offset in zip(group_units, offsets, strict=True):
            if unit.text == SILENT:
                timings.append(
                    {
                        "unit_id": unit.unit_id,
                        "offset": round(offset, 6),
                        "speech_start": round(offset, 6),
                        "speech_end": round(offset + 4.0 / speed, 6),
                        "events": [],
                    }
                )
                continue
            alignment = alignment_by_id[unit.unit_id]
            timings.append(
                {
                    "unit_id": unit.unit_id,
                    "offset": round(offset, 6),
                    "speech_start": round(
                        offset + float(alignment["speech_start"]) / speed, 6
                    ),
                    "speech_end": round(
                        offset + float(alignment["speech_end"]) / speed, 6
                    ),
                    "events": [
                        {
                            "unit_id": unit.unit_id,
                            "role": unit.role,
                            "start": offset + float(event["start"]) / speed,
                            "end": offset + float(event["end"]) / speed,
                            "text": str(event["text"]),
                        }
                        for event in alignment["events"]
                    ],
                }
            )
        group_timelines[group.group_id] = timings

    for row in direct["groups"]:
        group = next(item for item in plan.visual_groups if item.group_id == row["group_id"])
        row["actual_duration"] = float(group.audio_seconds or 4.0)
        row["audio_path"] = str(locked_by_group[group.group_id].relative_to(episode_dir))
        row["video_audio_path"] = str(driver_by_group[group.group_id].relative_to(episode_dir))
    atomic_write_json(direct_path, direct)
    atomic_write_json(
        episode_dir / "production_plan_sd25.json", plan.model_dump(mode="json")
    )

    def generate(group):
        row = direct_by_id[group.group_id]
        references = [novel_dir / ref["path"] for ref in row["references"]]
        for path in references:
            if not path.is_file():
                raise FileNotFoundError(path)
        output = episode_dir / group.raw_video_path
        duration = min(14.0, max(4.0, float(group.audio_seconds or 4.0) + 0.5))
        provider.create_video(
            row["prompt"],
            ImageResult(path=references[0]),
            output,
            duration,
            reference_audio=driver_by_group[group.group_id],
            additional_images=tuple(references[1:]),
        )
        first_frame = episode_dir / "work" / "first_frames" / f"{group.group_id}.jpeg"
        first_frame.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-ss", "0.12", "-i", str(output),
                "-frames:v", "1", "-q:v", "2", str(first_frame),
            ],
            check=True,
        )
        return {
            "group_id": group.group_id,
            "output": str(output.relative_to(episode_dir)),
            "duration": media_duration(output),
            "task_sidecar": str(
                output.with_suffix(output.suffix + ".task.json").relative_to(episode_dir)
            ),
            "first_frame": str(first_frame.relative_to(episode_dir)),
            "reference_count": len(references),
        }

    visual_rows = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(generate, group): group.group_id
            for group in plan.visual_groups
        }
        for future in as_completed(futures):
            result = future.result()
            visual_rows.append(result)
            print(
                json.dumps(
                    {
                        "stage": "sd25",
                        "done": result["group_id"],
                        "completed": len(visual_rows),
                        "total": len(futures),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    visual_rows.sort(key=lambda row: row["group_id"])
    atomic_write_json(
        episode_dir / "visual_generation_report.json",
        {
            "workflow": "sd25-direct-assets-no-keyframe-v1",
            "model": "sd2.5",
            "video_workers": 2,
            "keyframe_generation": False,
            "qwen_image_used": False,
            "minimax_h3_used": False,
            "groups": visual_rows,
        },
    )

    rows_by_group = {row["group_id"]: row for row in visual_rows}
    for group in plan.visual_groups:
        first_frame = episode_dir / rows_by_group[group.group_id]["first_frame"]
        for unit_id in group.unit_ids:
            unit = units[unit_id]
            unit.keyframe_path = str(first_frame.relative_to(episode_dir))
            unit.raw_video_path = group.raw_video_path
            unit.segment_path = group.segment_path
    face_report = evaluate_face_consistency(
        novel_dir=novel_dir,
        episode_dir=episode_dir,
        plan=plan,
        assets=assets,
    )
    atomic_write_json(episode_dir / "face_consistency_report.json", face_report)

    turn_segments = []
    delivery_timeline = []
    story_cursor = 0.0
    for group in plan.visual_groups:
        segment, duration = renderer.mux_visual_group(
            episode_dir / group.raw_video_path,
            locked_by_group[group.group_id],
            episode_dir / group.segment_path,
        )
        local_events = [
            event
            for timing in group_timelines[group.group_id]
            for event in timing["events"]
        ]
        turn_segments.append(
            {
                "unit_id": group.group_id,
                "role": "narrator",
                "segment": str(segment),
                "duration": duration,
                "audio_source": "locked_indextts",
                "subtitle_events": local_events,
            }
        )
        for timing in group_timelines[group.group_id]:
            if units[timing["unit_id"]].text == SILENT:
                continue
            delivery_timeline.append(
                {
                    "unit_id": timing["unit_id"],
                    "speech_start": story_cursor + float(timing["speech_start"]),
                    "speech_end": story_cursor + float(timing["speech_end"]),
                }
            )
        story_cursor += duration

    cover_source = novel_dir / assets.characters[0].primary_image
    cover = episode_dir / f"{args.video_id}_sd25_direct_cover.jpeg"
    ending = episode_dir / f"{args.video_id}_sd25_direct_ending.jpeg"
    renderer.make_cover(
        cover_source,
        cover,
        novel_title=bible.novel_title,
        art_title=episode_plan.video_title,
        episode_label="第01集",
    )
    ending_source = novel_dir / assets.locations[0].primary_image
    renderer.make_card(
        ending_source,
        ending,
        bible.novel_title,
        "本集完",
        episode_plan.next_preview,
    )
    intro_card = episode_dir / "work" / "series_intro.jpeg"
    renderer.normalize_jpeg(cover, intro_card)
    final_video = episode_dir / f"{args.video_id}_sd25_direct.mp4"
    final_video, ass, joined, subtitle_events = renderer.assemble_production(
        intro_card,
        ending,
        turn_segments,
        final_video,
        episode_dir / "work",
    )

    spoken_units = [unit for unit in plan.units if unit.text != SILENT]
    spoken_plan = plan.model_copy(update={"units": spoken_units})
    if args.skip_asr:
        asr_report = {
            "status": "skipped",
            "reason": "user requested preview generation before ASR",
            "cer": 999.0,
            "turns": [],
            "silent_visual_units_excluded": len(plan.units) - len(spoken_units),
        }
    else:
        asr_report = runtime._audit_delivered_asr(
            episode_dir,
            final_video,
            spoken_plan,
            delivery_timeline,
        )
        asr_report["silent_visual_units_excluded"] = len(plan.units) - len(spoken_units)
    atomic_write_json(episode_dir / "asr_report.json", asr_report)

    trace = {
        "novel_id": "ftj-anime-api10-3d-script-ab",
        "video_id": args.video_id,
        "source_title": episode.source_title,
        "source_start": episode.source_start,
        "source_end": episode.source_end,
        "source_text_sha256": plan.source_text_sha256,
        "style_fingerprint": plan.style_fingerprint,
        "workflow": "sd25-direct-assets-no-keyframe-v1",
        "keyframe_generation": False,
        "scenes": [scene.model_dump(mode="json") for scene in plan.scenes],
        "shots": [shot.model_dump(mode="json") for shot in plan.shots],
        "video_groups": direct["groups"],
        "turns": [
            {
                "unit_id": unit.unit_id,
                "source_quote": unit.source_quote,
                "text": "" if unit.text == SILENT else unit.text,
                "silent_visual_unit": unit.text == SILENT,
                "speaker_name": unit.speaker_name,
                "speaking": unit.speaking,
                "character_asset_ids": unit.character_asset_ids,
                "location_asset_id": unit.location_asset_id,
                "audio_path": unit.audio_path,
                "first_frame_path": unit.keyframe_path,
                "clip_path": unit.raw_video_path,
                "subtitle_alignment": unit.subtitle_alignment,
            }
            for unit in plan.units
        ],
    }
    atomic_write_json(episode_dir / "content_trace.json", trace)
    media_qc = inspect_media(
        final_video,
        cover,
        ending,
        ass,
        settings,
        episode_dir / "media_qc_report.json",
    )
    admission = evaluate_episode_admission(
        settings=settings,
        plan=spoken_plan,
        media_qc=media_qc,
        ass=ass,
        clean_video=joined,
        delivered_video=final_video,
        subtitle_events=subtitle_events,
        asr_report=asr_report,
        face_consistency_report=face_report,
    )
    atomic_write_json(episode_dir / "admission_report.json", admission)
    atomic_write_json(episode_dir / "qc_report.json", admission)
    report = {
        "workflow": "sd25-direct-assets-no-keyframe-v1",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "video_groups": len(plan.visual_groups),
        "video_workers": 2,
        "keyframe_generation": False,
        "qwen_image_used": False,
        "minimax_h3_used": False,
        "final_video": str(final_video),
        "duration_seconds": media_duration(final_video),
        "admission_passed": bool(admission.get("passed")),
        "submission_eligible": bool(admission.get("submission_eligible")),
        "preview_asr_deferred": bool(args.skip_asr),
    }
    atomic_write_json(episode_dir / "sd25_direct_run_report.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if args.skip_asr or admission.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
