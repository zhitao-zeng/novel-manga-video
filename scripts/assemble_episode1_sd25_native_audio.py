#!/usr/bin/env python3
"""Reassemble episode 1 while preserving every SD2.5 native audio track."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path

from novel_manga.admission import evaluate_episode_admission
from novel_manga.config import Settings
from novel_manga.models import EpisodePlan, StoryBible
from novel_manga.production_models import ProductionPlan
from novel_manga.qc import inspect_media
from novel_manga.render import Renderer
from novel_manga.util import atomic_write_json, media_duration


SILENT = "【无对白动作镜】"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    return parser.parse_args()


def normalize_native(
    source: Path,
    output: Path,
    delivery_duration: float | None = None,
) -> tuple[Path, float]:
    output.parent.mkdir(parents=True, exist_ok=True)
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    identity = hashlib.sha256(
        json.dumps(
            {
                "source_sha256": source_sha256,
                "delivery_duration": delivery_duration,
                "policy": "sd25-native-trim-v2",
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    meta = output.with_suffix(output.suffix + ".request.json")
    reusable = False
    if output.is_file() and meta.is_file():
        reusable = json.loads(meta.read_text(encoding="utf-8")).get(
            "request_sha256"
        ) == identity
    if not reusable:
        command = [
                "ffmpeg", "-y", "-v", "error", "-i", str(source),
                "-filter_complex",
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,fps=25,format=yuv420p,setpts=PTS-STARTPTS[v];"
                "[0:a]aresample=48000,asetpts=PTS-STARTPTS[a]",
                "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                "-preset", "veryfast", "-crf", "20", "-r", "25",
                "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                "-movflags", "+faststart",
            ]
        if delivery_duration is not None:
            command.extend(["-t", f"{delivery_duration:.3f}"])
        command.extend(["-shortest", str(output)])
        subprocess.run(command, check=True)
        atomic_write_json(
            meta,
            {
                "request_sha256": identity,
                "source_sha256": source_sha256,
                "delivery_duration": delivery_duration,
                "audio_source": "sd25_native_original",
            },
        )
    return output, media_duration(output)


def extract_frame(source: Path, output: Path, *, near_end: bool = False) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    start = max(0.0, media_duration(source) - 0.12) if near_end else 0.08
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(output),
        ],
        check=True,
    )
    return output


def subtitle_events_for_group(
    unit_ids: list[str],
    units: dict,
    duration: float,
) -> list[dict]:
    spoken = [units[unit_id] for unit_id in unit_ids if units[unit_id].text != SILENT]
    if not spoken:
        return []
    start = 0.20
    end = max(start + 0.2, duration - 0.25)
    weights = [max(1, len(unit.text)) for unit in spoken]
    total = sum(weights)
    cursor = start
    events = []
    for index, (unit, weight) in enumerate(zip(spoken, weights, strict=True)):
        unit_end = end if index == len(spoken) - 1 else cursor + (end - start) * weight / total
        events.append(
            {
                "unit_id": unit.unit_id,
                "role": unit.role,
                "start": round(cursor, 6),
                "end": round(max(cursor + 0.2, unit_end), 6),
                "text": unit.text,
            }
        )
        cursor = unit_end
    return events


def main() -> int:
    args = parse_args()
    started = time.monotonic()
    episode_dir = args.episode_dir.resolve()
    work = episode_dir / "work_native_audio"
    direct = json.loads((episode_dir / "sd25_direct_plan.json").read_text(encoding="utf-8"))
    plan = ProductionPlan.model_validate_json(
        (episode_dir / "production_plan_sd25.json").read_text(encoding="utf-8")
    )
    episode_plan = EpisodePlan.model_validate_json(
        (episode_dir / "episode_plan.json").read_text(encoding="utf-8")
    )
    bible = StoryBible.model_validate_json(
        (episode_dir.parent / "story_bible.json").read_text(encoding="utf-8")
    )
    groups = {group.group_id: group for group in plan.visual_groups}
    units = {unit.unit_id: unit for unit in plan.units}
    rows = []
    turn_segments = []
    for item in direct["groups"]:
        group_id = item["group_id"]
        group = groups[group_id]
        raw = episode_dir / group.raw_video_path
        if not raw.is_file():
            raise FileNotFoundError(raw)
        segment = work / "segments" / f"{group_id}.mp4"
        delivery_duration = item.get("delivery_duration")
        segment, duration = normalize_native(
            raw,
            segment,
            float(delivery_duration) if delivery_duration is not None else None,
        )
        local_events = subtitle_events_for_group(group.unit_ids, units, duration)
        turn_segments.append(
            {
                "unit_id": group_id,
                "role": "narrator",
                "segment": str(segment),
                "duration": duration,
                "audio_source": "sd25_native_original",
                "subtitle_events": local_events,
            }
        )
        rows.append(
            {
                "group_id": group_id,
                "raw_video": str(raw.relative_to(episode_dir)),
                "segment": str(segment.relative_to(episode_dir)),
                "selected_source": "sd25_native_original",
                "audio_codec": "aac",
                "native_duration": round(media_duration(raw), 6),
                "requested_delivery_duration": delivery_duration,
                "delivered_segment_duration": round(duration, 6),
                "subtitle_timing": "coarse_native_clip_bounds",
            }
        )

    base = Settings.from_env(
        provider="phanrouter",
        output_root=episode_dir.parent,
        admission_mode="preview",
    )
    settings = replace(
        base,
        video_model="sd2.5",
        final_audio_policy="sd25_native_original",
        intro_seconds=0.0,
        outro_seconds=0.0,
        fps=25,
        width=1080,
        height=1920,
    )
    settings.validate()
    renderer = Renderer(settings)
    cover = episode_dir / f"{args.video_id}_cover.jpeg"
    ending = episode_dir / f"{args.video_id}_ending.jpeg"
    endpoint_dir = work / "endpoints"
    first_source = Path(turn_segments[0]["segment"])
    last_source = Path(turn_segments[-1]["segment"])
    first_frame = extract_frame(first_source, endpoint_dir / "first.jpeg")
    last_frame = extract_frame(
        last_source,
        endpoint_dir / "last.jpeg",
        near_end=True,
    )
    renderer.make_cover(
        first_frame,
        cover,
        novel_title=bible.novel_title,
        art_title=episode_plan.video_title,
        episode_label="第01集",
    )
    renderer.make_card(
        last_frame,
        ending,
        bible.novel_title,
        "本集完",
        episode_plan.next_preview,
    )
    intro_card = episode_dir / "work" / "series_intro.jpeg"
    intro_card.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cover, intro_card)
    final_video = episode_dir / f"{args.video_id}_sd25_native_audio.mp4"
    final_video, ass, joined, subtitle_events = renderer.assemble_production(
        intro_card,
        ending,
        turn_segments,
        final_video,
        work,
    )
    selection = {
        "schema_version": 1,
        "policy": "sd25_native_original_all_groups",
        "group_count": len(rows),
        "locked_tts_used_in_final": False,
        "sd25_native_audio_groups": len(rows),
        "groups": rows,
    }
    atomic_write_json(episode_dir / "native_audio_selection_report.json", selection)
    asr_report = {
        "status": "skipped",
        "reason": "native-audio preview requested before ASR",
        "cer": 999.0,
        "turns": [],
    }
    atomic_write_json(episode_dir / "asr_native_audio_report.json", asr_report)
    trace = {
        "novel_id": "ftj-anime-api10-3d-script-ab",
        "video_id": args.video_id,
        "source_title": plan.source_title,
        "source_text_sha256": plan.source_text_sha256,
        "style_fingerprint": plan.style_fingerprint,
        "workflow": "sd25-hybrid-native-audio-v2",
        "keyframe_generation": bool(direct.get("keyframe_generation")),
        "locked_tts_used_in_final": False,
        "audio_policy": "sd25_native_original_all_groups",
        "shots": [shot.model_dump(mode="json") for shot in plan.shots],
        "groups": rows,
        "turns": [
            {
                "unit_id": unit.unit_id,
                "source_quote": unit.source_quote,
                "text": "" if unit.text == SILENT else unit.text,
                "silent_visual_unit": unit.text == SILENT,
                "speaker_name": unit.speaker_name,
                "clip_path": unit.raw_video_path,
                "audio_source": "sd25_native_original",
                "subtitle_alignment": (
                    "none" if unit.text == SILENT else "coarse_native_clip_bounds"
                ),
            }
            for unit in plan.units
        ],
    }
    atomic_write_json(episode_dir / "content_trace_native_audio.json", trace)
    media_qc = inspect_media(
        final_video,
        cover,
        ending,
        ass,
        settings,
        episode_dir / "media_qc_native_audio_report.json",
    )
    face_report_path = episode_dir / "face_consistency_report.json"
    face_report = (
        json.loads(face_report_path.read_text(encoding="utf-8"))
        if face_report_path.is_file()
        else {"status": "unavailable", "reason": "identity gate not run yet"}
    )
    spoken_plan = plan.model_copy(
        update={"units": [unit for unit in plan.units if unit.text != SILENT]}
    )
    video_quality_path = episode_dir / "episode_video_quality_report.json"
    video_quality_report = (
        json.loads(video_quality_path.read_text(encoding="utf-8"))
        if video_quality_path.is_file()
        else None
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
        video_quality_report=video_quality_report,
    )
    atomic_write_json(episode_dir / "admission_native_audio_report.json", admission)
    report = {
        "workflow": "sd25-hybrid-native-audio-v2",
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "group_count": len(rows),
        "sd25_native_audio_groups": len(rows),
        "locked_tts_used_in_final": False,
        "final_video": str(final_video),
        "duration_seconds": media_duration(final_video),
        "media_qc_passed": bool(media_qc.get("passed")),
        "submission_eligible": bool(admission.get("submission_eligible")),
        "asr_deferred": True,
    }
    atomic_write_json(episode_dir / "sd25_native_audio_run_report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if media_qc.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
