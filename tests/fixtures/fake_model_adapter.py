#!/usr/bin/env python3
"""Deterministic command-provider fixture for local integration tests only."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("mode", choices=("planner", "image", "video", "tts", "asr"))
    root.add_argument("--output", type=Path, required=True)
    root.add_argument("--prompt")
    root.add_argument("--reference")
    root.add_argument("--image", type=Path)
    root.add_argument("--reference-audio", type=Path)
    root.add_argument("--duration", type=float, default=4.0)
    root.add_argument("--fps", type=int, default=25)
    root.add_argument("--width", type=int, default=1080)
    root.add_argument("--height", type=int, default=1920)
    root.add_argument("--text", default="")
    root.add_argument("--voice")
    root.add_argument("--instructions")
    root.add_argument("--audio", type=Path)
    root.add_argument("--video", type=Path)
    root.add_argument("--unit-id")
    root.add_argument("--speaking", choices=("true", "false"))
    root.add_argument("--operation", choices=("build_bible", "plan_episode"))
    root.add_argument("--input", type=Path)
    return root


def main() -> None:
    args = parser().parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "planner":
        request = json.loads(args.input.read_text(encoding="utf-8"))
        if args.operation == "build_bible":
            title = request["novel"]["title"]
            payload = {
                "novel_title": title,
                "genre": "测试悬疑",
                "visual_style": "二维国漫",
                "palette": "青蓝",
                "characters": [
                    {
                        "name": "林晚",
                        "role": "主角",
                        "appearance": "黑发少女",
                        "wardrobe": "蓝色风衣",
                    }
                ],
                "locations": ["门外"],
                "continuity_rules": ["角色与场景保持一致"],
                "style_fingerprint": "filled-by-runtime",
            }
        else:
            source = request["episode"]["source_text"]
            payload = {
                "video_title": request["episode"]["source_title"],
                "hook": "不要开门",
                "summary": source,
                "shots": [
                    {
                        "index": 1,
                        "narration": source,
                        "subtitle": source,
                        "visual_prompt": "门外的林晚",
                        "motion_prompt": "轻微推镜",
                        "characters": ["林晚"],
                        "location": "门外",
                        "source_quote": source,
                        "turns": [
                            {
                                "role": "林晚",
                                "speaker_name": "林晚",
                                "text": "不要开门。",
                                "speaking": True,
                                "source_quote": source,
                            }
                        ],
                    }
                ],
            }
        args.output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return
    if args.mode == "image":
        image = Image.new("RGB", (args.width, args.height), (36, 58, 92))
        draw = ImageDraw.Draw(image)
        draw.ellipse((220, 260, 860, 900), fill=(232, 202, 176), outline=(20, 20, 30), width=12)
        draw.rectangle((260, 900, 820, 1760), fill=(45, 85, 130), outline=(235, 195, 100), width=10)
        image.save(args.output, "JPEG", quality=92)
        return
    if args.mode == "video":
        assert args.image is not None
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(args.image),
                "-t", f"{args.duration:.6f}", "-vf", f"scale={args.width}:{args.height},format=yuv420p",
                "-r", str(args.fps), "-an", "-c:v", "libx264", "-preset", "ultrafast", str(args.output),
            ],
            check=True,
        )
        return
    if args.mode == "tts":
        duration = max(2.5, min(8.0, len(args.text) / 3.5))
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=330:sample_rate=24000", "-t", f"{duration:.6f}",
                "-af", "volume=-8dB", "-c:a", "pcm_s16le", str(args.output),
            ],
            check=True,
            capture_output=True,
        )
        return
    if args.mode == "asr":
        args.output.write_text(
            json.dumps({"backend": "fixture-exact-asr", "hypothesis": args.text}, ensure_ascii=False),
            encoding="utf-8",
        )
        return


if __name__ == "__main__":
    main()
