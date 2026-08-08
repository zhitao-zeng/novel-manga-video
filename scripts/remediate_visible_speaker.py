#!/usr/bin/env python3
"""Regenerate dialogue units whose visible speaker identity is incorrect."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from novel_manga.config import Settings
from novel_manga.providers.phanrouter import PhanRouterMediaProvider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--production-plan", type=Path, required=True)
    parser.add_argument("--series-manifest", type=Path, required=True)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--speaker-name", required=True)
    parser.add_argument("--workers", type=int, default=2)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def provider() -> PhanRouterMediaProvider:
    return PhanRouterMediaProvider(
        Settings(
            provider="phanrouter",
            phanrouter_api_key=os.environ["PHANROUTER_API_KEY"],
            image_model=os.getenv("PHANROUTER_IMAGE_MODEL", "gpt-image-2"),
            video_model=os.getenv("PHANROUTER_VIDEO_MODEL", "sd2.0"),
            request_timeout=180.0,
            poll_timeout=1200.0,
        )
    )


def main() -> None:
    args = parse_args()
    if not 1 <= args.workers <= 2:
        raise ValueError("workers must be 1 or 2")
    plan = json.loads(args.production_plan.read_text(encoding="utf-8"))
    manifest = json.loads(args.series_manifest.read_text(encoding="utf-8"))
    character = next(row for row in manifest["characters"] if row["name"] == args.speaker_name)
    reference = args.novel_dir / character["primary_image"]
    character_spec = json.loads(
        (args.novel_dir / character["spec_path"]).read_text(encoding="utf-8")
    )
    identity = "；".join(
        str(character_spec.get(field, "")).strip()
        for field in ("name", "gender", "age", "appearance", "wardrobe")
        if str(character_spec.get(field, "")).strip()
    )
    locations = {row["asset_id"]: row["name"] for row in manifest["locations"]}
    units = [
        row for row in plan["units"]
        if row.get("speaking") and row.get("speaker_name") == args.speaker_name
    ]
    if not units:
        raise ValueError(f"no visible dialogue units found for {args.speaker_name}")

    def generate(unit: dict) -> dict:
        location = locations[unit["location_asset_id"]]
        directory = args.output_dir / unit["unit_id"]
        keyframe = directory / "keyframe.jpeg"
        clip = directory / "clip.mp4"
        image_prompt = (
            f"系列风格指纹 {plan['style_fingerprint']}。二维国漫竖屏漫剧。"
            f"严格保持参考图中{args.speaker_name}的固定人物设定：{identity}。"
            f"场景为{location}，背景适度虚化。单人胸像正脸或四分之三近景，只有{args.speaker_name}出镜，"
            "脸和完整嘴部位于画面中央安全区，嘴巴自然闭合、无遮挡。"
            "不要出现被测试者、其他前景人物、文字、字幕、气泡、Logo、水印、真人或3D。"
        )
        media = provider()
        image = media.create_image(image_prompt, keyframe, reference=reference)
        motion_prompt = (
            f"严格使用参考音频作为唯一对白。{args.speaker_name}单人在固定近景中逐字说出：‘{unit['text']}’。"
            f"只有{args.speaker_name}开口，身份、脸型、发型和固定服装保持稳定；"
            "脸和嘴全程清晰，口型逐字跟随参考音频，音频结束后自然闭嘴至少0.3秒。"
            "不改词、不漏词、不增加声音、不切镜、不转身、不遮挡嘴部，不出现其他说话人。"
        )
        media.create_video(
            motion_prompt,
            image,
            clip,
            duration=min(14.0, max(4.0, float(unit["audio_seconds"]) + 0.5)),
            reference_audio=args.episode_dir / unit["audio_path"],
        )
        return {
            "unit_id": unit["unit_id"],
            "speaker_name": args.speaker_name,
            "text": unit["text"],
            "keyframe": str(keyframe),
            "clip": str(clip),
            "reference_asset": str(reference),
            "reference_audio": str(args.episode_dir / unit["audio_path"]),
            "keyframe_sha256": sha256(keyframe),
            "clip_sha256": sha256(clip),
            "exact_dialogue_in_prompt": unit["text"] in motion_prompt,
        }

    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(generate, unit): unit["unit_id"] for unit in units}
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(f"remediated unit={row['unit_id']}", flush=True)
    report = {
        "schema_version": 1,
        "reason": "visible speaker identity correction",
        "speaker_name": args.speaker_name,
        "model": os.getenv("PHANROUTER_VIDEO_MODEL", "sd2.0"),
        "unit_count": len(rows),
        "workers": args.workers,
        "units": sorted(rows, key=lambda row: row["unit_id"]),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"unit_count": len(rows), "report": str(args.output_dir / 'report.json')}, ensure_ascii=False))


if __name__ == "__main__":
    main()
