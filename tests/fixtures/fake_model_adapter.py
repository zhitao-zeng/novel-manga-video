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
    root.add_argument("--additional-image", type=Path, action="append")
    root.add_argument("--reference-audio", type=Path)
    root.add_argument("--duration", type=float, default=4.0)
    root.add_argument("--fps", type=int, default=25)
    root.add_argument("--width", type=int, default=1080)
    root.add_argument("--height", type=int, default=1920)
    root.add_argument("--text", default="")
    root.add_argument("--voice")
    root.add_argument("--instructions")
    root.add_argument("--speed", type=float)
    root.add_argument("--audio", type=Path)
    root.add_argument("--video", type=Path)
    root.add_argument("--unit-id")
    root.add_argument("--speaking", choices=("true", "false"))
    root.add_argument(
        "--operation",
        choices=(
            "build_bible",
            "diagnose_episode",
            "plan_episode",
            "review_episode",
            "update_series_state",
        ),
    )
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
        elif args.operation == "diagnose_episode":
            episode = request["episode"]
            source = episode["source_text"]
            payload = {
                "source_chapter": episode["source_title"],
                "density": "sparse",
                "core_event": "林晚阻止开门",
                "chapter_start_state": "林晚听见门外动静",
                "chapter_end_state": "林晚阻止开门",
                "episode_state_change": "林晚确认门外存在危险",
                "strongest_hook_candidate": "门外脚步逼近",
                "hook_source_quote": source,
                "ending_type": "decision",
                "events": [
                    {
                        "event_id": "event_001",
                        "order": 1,
                        "description": "林晚阻止开门",
                        "source_quote": source,
                        "importance": "critical",
                        "narrative_role": "resolution",
                        "characters": ["林晚"],
                    }
                ],
            }
        elif args.operation == "review_episode":
            plan = request["episode_plan"]
            turns = [turn for shot in plan["shots"] for turn in shot["turns"]]
            payload = {
                "passed": True,
                "script_char_count": sum(len(turn["text"]) for turn in turns),
                "shot_count": len(plan["shots"]),
                "turn_count": len(turns),
                "critical_event_coverage": 1,
                "causal_chain_complete": True,
                "character_introductions_complete": True,
                "opening_no_spoiler": True,
                "ending_at_chapter_boundary": True,
                "future_content_used": False,
                "issues": [],
            }
        elif args.operation == "update_series_state":
            episode = request["episode"]
            source = episode["source_text"]
            evidence = {
                "statement": "林晚阻止开门",
                "source_episode": episode["index"],
                "source_quote": source,
            }
            payload = {
                "current_episode": episode["index"],
                "timeline": [evidence],
                "characters": [
                    {
                        "name": "林晚",
                        "current_location": "门内",
                        "emotional_state": "警觉",
                        "current_goal": "阻止开门",
                        "evidence": evidence,
                    }
                ],
                "previous_episode_end": {
                    "location": "门内",
                    "action": "林晚阻止开门",
                    "final_visual": "林晚挡在门前",
                    "evidence": evidence,
                },
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
                        "event_ids": ["event_001"],
                        "performance_plan": {
                            "objective": "林晚由警惕转为坚决",
                            "start_state": "林晚尚未完全转向门口",
                            "motion_beats": [
                                {
                                    "phase": "opening",
                                    "trigger": "听见门外脚步",
                                    "action": "眼睛先移向门口，随后侧头",
                                    "reaction": "肩膀绷紧，身体重心后移",
                                    "expression_transition": "从平静转为警惕",
                                },
                                {
                                    "phase": "resolution",
                                    "trigger": "确认危险",
                                    "action": "抬手阻止开门",
                                    "reaction": "身体挡向门口",
                                    "expression_transition": "从警惕转为坚决",
                                },
                            ],
                            "end_state": "林晚停在门前，目光坚定",
                        },
                        "camera_plan": {
                            "start_position": "林晚左后方中景",
                            "camera_beats": [
                                {
                                    "phase": "opening",
                                    "trajectory": "随转头向右横移",
                                    "framing": "进入胸像近景",
                                    "parallax": "门框快于远处走廊移动",
                                },
                                {
                                    "phase": "resolution",
                                    "trajectory": "沿短弧线减速停住",
                                    "framing": "停在右前方四分之三近景",
                                    "parallax": "门框、人物和走廊形成三层视差",
                                },
                            ],
                            "end_position": "林晚右前方稳定近景",
                        },
                        "turns": [
                            {
                                "role": "林晚",
                                "speaker_name": "林晚",
                                "text": "不要开门。",
                                "speaking": True,
                                "source_quote": source,
                            },
                            {
                                "role": "narrator",
                                "speaker_name": "旁白",
                                "text": "她挡在门前。",
                                "speaking": False,
                                "source_quote": source,
                            },
                        ],
                    }
                ],
                "adaptation_ledger": [
                    {
                        "event_id": "event_001",
                        "disposition": "preserved",
                        "shot_indexes": [1],
                        "rationale": "用动作和原文台词直接表现",
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
