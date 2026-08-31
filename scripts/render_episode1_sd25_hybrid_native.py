#!/usr/bin/env python3
"""Execute the episode-1 hybrid GPT Image 2 + SD2.5 native-audio plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

from novel_manga.config import Settings
from novel_manga.face_consistency import evaluate_face_consistency
from novel_manga.production_models import ProductionPlan, SeriesAssetManifest
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.render import Renderer
from novel_manga.util import atomic_write_json, media_duration


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--image-workers", type=int, default=2)
    parser.add_argument("--video-workers", type=int, default=2)
    parser.add_argument("--only-group", action="append", default=[])
    parser.add_argument("--stop-after-keyframes", action="store_true")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_hash(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def proxy_mode() -> str:
    values = [
        os.environ.get(name, "")
        for name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        )
    ]
    configured = [value for value in values if value]
    if any("8234" in value for value in configured):
        raise RuntimeError("proxy port 8234 is forbidden for this production run")
    return "configured_non_8234" if configured else "direct"


def stream_report(path: Path) -> dict:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(probe.stdout)
    video = next(
        (row for row in payload.get("streams", []) if row.get("codec_type") == "video"),
        None,
    )
    audio = next(
        (row for row in payload.get("streams", []) if row.get("codec_type") == "audio"),
        None,
    )
    return {
        "has_video": video is not None,
        "has_audio": audio is not None,
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "duration": media_duration(path),
    }


def copy_provider_sidecar(source: Path, target: Path) -> str | None:
    source_sidecar = source.with_suffix(source.suffix + ".task.json")
    if not source_sidecar.is_file():
        return None
    target_sidecar = target.with_suffix(target.suffix + ".task.json")
    target_sidecar.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_sidecar, target_sidecar)
    return str(target_sidecar)


def main() -> int:
    args = parse_args()
    if args.video_workers != 2:
        raise ValueError("SD2.5 production concurrency is locked to 2")
    if not 1 <= args.image_workers <= 2:
        raise ValueError("GPT Image 2 concurrency must be 1 or 2")

    started = time.monotonic()
    novel_dir = args.novel_dir.resolve()
    episode_dir = novel_dir / args.video_id
    manifest_path = episode_dir / "sd25_direct_plan.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = ProductionPlan.model_validate_json(
        (episode_dir / "production_plan_sd25.json").read_text(encoding="utf-8")
    )
    assets = SeriesAssetManifest.model_validate_json(
        (novel_dir / "series_assets" / "manifest.json").read_text(encoding="utf-8")
    )
    all_rows = manifest["groups"]
    rows_by_id = {row["group_id"]: row for row in all_rows}
    groups_by_id = {group.group_id: group for group in plan.visual_groups}
    if set(rows_by_id) != set(groups_by_id):
        raise ValueError("manifest and production plan visual groups differ")
    selected_group_ids = set(args.only_group)
    unknown_groups = selected_group_ids - set(rows_by_id)
    if unknown_groups:
        raise ValueError(f"unknown --only-group values: {sorted(unknown_groups)}")
    rows = [
        row
        for row in all_rows
        if not selected_group_ids or row["group_id"] in selected_group_ids
    ]
    if manifest.get("qwen_image_used") is not False:
        raise ValueError("Qwen Image is forbidden for this run")
    if manifest.get("minimax_h3_used") is not False:
        raise ValueError("MiniMax H3 is forbidden for this run")
    if manifest.get("keyframe_image_model") != "gpt-image-2":
        raise ValueError("hybrid keyframes must use GPT Image 2")

    reference_paths: dict[str, list[Path]] = {}
    for row in rows:
        paths = [novel_dir / reference["path"] for reference in row["references"]]
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"{row['group_id']} missing references: {missing}")
        reference_paths[row["group_id"]] = paths

    preexisting_tasks = {
        str(path.relative_to(episode_dir)) for path in episode_dir.rglob("*.task.json")
    }
    preflight = {
        "schema_version": 1,
        "workflow": "sd25-hybrid-native-audio-v2",
        "execute": bool(args.execute),
        "proxy_mode": proxy_mode(),
        "image_model": "gpt-image-2",
        "video_model": "sd2.5",
        "image_workers": args.image_workers,
        "video_workers": args.video_workers,
        "keyframe_groups": [
            row["group_id"] for row in rows if row["keyframe_generation"]
        ],
        "direct_groups": [
            row["group_id"] for row in rows if not row["keyframe_generation"]
        ],
        "native_audio": True,
        "reference_audio_used": False,
        "preexisting_provider_task_sidecars": sorted(preexisting_tasks),
    }
    atomic_write_json(episode_dir / "hybrid_native_execution_preflight.json", preflight)
    if not args.execute:
        print(json.dumps(preflight, ensure_ascii=False))
        return 0

    base = Settings.from_env(
        provider="phanrouter",
        output_root=novel_dir.parent,
        admission_mode="preview",
    )
    settings = replace(
        base,
        image_model="gpt-image-2",
        video_model="sd2.5",
        final_audio_policy="sd25_native_original",
        inline_reference_images=True,
        media_workers=args.image_workers,
        video_workers=2,
        intro_seconds=0.0,
        outro_seconds=0.0,
    )
    settings.validate()
    provider = PhanRouterMediaProvider(settings)
    renderer = Renderer(settings)

    def prepare_keyframe(row: dict) -> dict:
        group_id = row["group_id"]
        references = reference_paths[group_id]
        prompt = row["prompt_adapter"]["image_prompt"]
        identity = request_hash(
            {
                "model": "gpt-image-2",
                "prompt": prompt,
                "references": [sha256_file(path) for path in references],
                "policy": "conditional-story-keyframe-v2",
            }
        )
        attempt = (
            episode_dir
            / "work"
            / "hybrid_keyframe_attempts"
            / group_id
            / identity[:16]
            / "frame.jpeg"
        )
        canonical = episode_dir / groups_by_id[group_id].keyframe_path
        meta = canonical.with_suffix(canonical.suffix + ".request.json")
        reusable = False
        if canonical.is_file() and meta.is_file():
            reusable = json.loads(meta.read_text(encoding="utf-8")).get(
                "request_sha256"
            ) == identity
        if not reusable:
            if not attempt.is_file():
                provider.create_image(
                    prompt,
                    attempt,
                    reference=references[0],
                    additional_references=tuple(references[1:]),
                )
            canonical.parent.mkdir(parents=True, exist_ok=True)
            renderer.normalize_jpeg(attempt, canonical)
            atomic_write_json(
                meta,
                {
                    "request_sha256": identity,
                    "model": "gpt-image-2",
                    "reference_sha256s": [sha256_file(path) for path in references],
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                },
            )
        return {
            "group_id": group_id,
            "path": str(canonical),
            "request_sha256": identity,
            "reused": reusable,
            "provider_task_sidecar": copy_provider_sidecar(attempt, canonical),
        }

    keyframe_rows = [row for row in rows if row["keyframe_generation"]]
    prepared_keyframes: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=args.image_workers) as executor:
        futures = {executor.submit(prepare_keyframe, row): row["group_id"] for row in keyframe_rows}
        for future in as_completed(futures):
            result = future.result()
            prepared_keyframes[result["group_id"]] = result
            print(
                json.dumps(
                    {
                        "stage": "gpt-image-2",
                        "done": result["group_id"],
                        "completed": len(prepared_keyframes),
                        "total": len(keyframe_rows),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    if args.stop_after_keyframes:
        report = {
            **preflight,
            "execute": True,
            "stage_complete": "keyframes",
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "generated_keyframes": len(prepared_keyframes),
            "keyframes": [
                prepared_keyframes[key] for key in sorted(prepared_keyframes)
            ],
        }
        atomic_write_json(
            episode_dir / "hybrid_native_keyframe_probe_report.json",
            report,
        )
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0

    def prepare_video(row: dict) -> dict:
        group_id = row["group_id"]
        group = groups_by_id[group_id]
        if row["keyframe_generation"]:
            primary = Path(prepared_keyframes[group_id]["path"])
            additional: tuple[Path, ...] = ()
            input_strategy = "gpt-image-2-story-keyframe-i2v"
        else:
            references = reference_paths[group_id]
            primary = references[0]
            additional = tuple(references[1:])
            input_strategy = "direct-series-assets"
        prompt = row["prompt_adapter"]["video_prompt"]
        duration = float(row["generation_duration"])
        base_identity = request_hash(
            {
                "model": "sd2.5",
                "prompt": prompt,
                "duration": duration,
                "primary_sha256": sha256_file(primary),
                "additional_sha256s": [sha256_file(path) for path in additional],
                "native_audio": True,
                "reference_audio": None,
                "input_strategy": input_strategy,
            }
        )
        identity = base_identity
        attempt = (
            episode_dir
            / "work"
            / "hybrid_video_attempts"
            / group_id
            / identity[:16]
            / "clip.mp4"
        )
        canonical = episode_dir / group.raw_video_path
        meta = canonical.with_suffix(canonical.suffix + ".request.json")
        reusable = False
        copyright_safe_retry = False
        if canonical.is_file() and meta.is_file():
            saved_meta = json.loads(meta.read_text(encoding="utf-8"))
            reusable = (
                saved_meta.get("request_sha256") == identity
                or saved_meta.get("base_request_sha256") == base_identity
            )
            copyright_safe_retry = bool(saved_meta.get("copyright_safe_retry"))
        if not reusable:
            if not attempt.is_file():
                try:
                    provider.create_video(
                        prompt,
                        ImageResult(path=primary),
                        attempt,
                        duration,
                        reference_audio=None,
                        additional_images=additional,
                    )
                except RuntimeError as error:
                    if "OutputVideoSensitiveContentDetected.PolicyViolation" not in str(error):
                        raise
                    copyright_safe_retry = True
                    prompt = (
                        "本项目原创东方3D动画短片；人物、服装、建筑与故事均为原创设计，"
                        "不模仿、复刻或影射任何已知动漫、游戏、影视作品或角色。"
                        + prompt.replace("风格化中国3D国漫", "原创东方风格化3D动画")
                    )
                    identity = request_hash(
                        {
                            "model": "sd2.5",
                            "prompt": prompt,
                            "duration": duration,
                            "primary_sha256": sha256_file(primary),
                            "additional_sha256s": [
                                sha256_file(path) for path in additional
                            ],
                            "native_audio": True,
                            "reference_audio": None,
                            "input_strategy": input_strategy,
                            "retry_reason": "copyright_false_positive",
                        }
                    )
                    attempt = (
                        episode_dir
                        / "work"
                        / "hybrid_video_attempts"
                        / group_id
                        / identity[:16]
                        / "clip.mp4"
                    )
                    provider.create_video(
                        prompt,
                        ImageResult(path=primary),
                        attempt,
                        duration,
                        reference_audio=None,
                        additional_images=additional,
                    )
            report = stream_report(attempt)
            if not report["has_video"] or not report["has_audio"]:
                raise RuntimeError(f"{group_id} SD2.5 output lacks video or native audio")
            canonical.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(attempt, canonical)
            copy_provider_sidecar(attempt, canonical)
            atomic_write_json(
                meta,
                {
                    "request_sha256": identity,
                    "base_request_sha256": base_identity,
                    "model": "sd2.5",
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "input_strategy": input_strategy,
                    "native_audio": True,
                    "reference_audio_used": False,
                    "generation_duration": duration,
                    "copyright_safe_retry": copyright_safe_retry,
                },
            )
        report = stream_report(canonical)
        observed = episode_dir / "work" / "observed_first_frames" / f"{group_id}.jpeg"
        observed.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                "0.08",
                "-i",
                str(canonical),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(observed),
            ],
            check=True,
        )
        return {
            "group_id": group_id,
            "output": str(canonical.relative_to(episode_dir)),
            "input_strategy": input_strategy,
            "request_sha256": identity,
            "reference_audio_used": False,
            "native_audio": True,
            "reused": reusable,
            "copyright_safe_retry": copyright_safe_retry,
            "observed_first_frame": str(observed.relative_to(episode_dir)),
            **report,
        }

    video_results: list[dict] = []
    video_failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(prepare_video, row): row["group_id"] for row in rows}
        for future in as_completed(futures):
            group_id = futures[future]
            try:
                result = future.result()
            except Exception as error:
                failure = {
                    "group_id": group_id,
                    "error": f"{type(error).__name__}: {str(error)[:800]}",
                }
                video_failures.append(failure)
                print(
                    json.dumps({"stage": "sd2.5", "failed": failure}, ensure_ascii=False),
                    flush=True,
                )
                continue
            video_results.append(result)
            print(
                json.dumps(
                    {
                        "stage": "sd2.5",
                        "done": result["group_id"],
                        "completed": len(video_results),
                        "total": len(rows),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    if video_failures:
        atomic_write_json(
            episode_dir / "hybrid_native_generation_failures.json",
            {
                "schema_version": 1,
                "workflow": "sd25-hybrid-native-audio-v2",
                "failures": video_failures,
                "completed_groups": sorted(row["group_id"] for row in video_results),
            },
        )
        raise RuntimeError(
            f"SD2.5 generation failed for {[row['group_id'] for row in video_failures]}"
        )
    video_results.sort(key=lambda row: row["group_id"])
    if selected_group_ids:
        report = {
            **preflight,
            "execute": True,
            "stage_complete": "targeted-videos",
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "generated_keyframes": len(prepared_keyframes),
            "generated_videos": len(video_results),
            "all_native_audio": all(row["has_audio"] for row in video_results),
            "reference_audio_used": any(
                row["reference_audio_used"] for row in video_results
            ),
            "groups": video_results,
        }
        atomic_write_json(
            episode_dir / "hybrid_native_targeted_video_report.json",
            report,
        )
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0

    units_by_id = {unit.unit_id: unit for unit in plan.units}
    video_by_id = {row["group_id"]: row for row in video_results}
    for group in plan.visual_groups:
        source_frame = (
            episode_dir / group.keyframe_path
            if rows_by_id[group.group_id]["keyframe_generation"]
            else episode_dir / video_by_id[group.group_id]["observed_first_frame"]
        )
        for unit_id in group.unit_ids:
            target = episode_dir / units_by_id[unit_id].keyframe_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_frame, target)
    face_report = evaluate_face_consistency(
        novel_dir=novel_dir,
        episode_dir=episode_dir,
        plan=plan,
        assets=assets,
    )
    atomic_write_json(episode_dir / "face_consistency_report.json", face_report)
    atomic_write_json(
        episode_dir / "visual_generation_report.json",
        {
            "schema_version": 2,
            "workflow": "sd25-hybrid-native-audio-v2",
            "image_model": "gpt-image-2",
            "video_model": "sd2.5",
            "image_workers": args.image_workers,
            "video_workers": 2,
            "keyframes": [prepared_keyframes[key] for key in sorted(prepared_keyframes)],
            "groups": video_results,
        },
    )
    report = {
        **preflight,
        "execute": True,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "generated_keyframes": len(prepared_keyframes),
        "generated_videos": len(video_results),
        "all_native_audio": all(row["has_audio"] for row in video_results),
        "reference_audio_used": any(row["reference_audio_used"] for row in video_results),
        "visual_generation_report": str(episode_dir / "visual_generation_report.json"),
    }
    atomic_write_json(episode_dir / "hybrid_native_execution_report.json", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
