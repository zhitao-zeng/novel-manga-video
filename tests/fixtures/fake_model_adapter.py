#!/usr/bin/env python3
"""Deterministic command-provider fixture for local integration tests only."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("mode", choices=("planner", "image", "video", "asr"))
    root.add_argument("--output", type=Path, required=True)
    root.add_argument("--prompt")
    root.add_argument("--reference")
    root.add_argument("--image", type=Path)
    root.add_argument("--additional-image", type=Path, action="append")
    root.add_argument("--duration", type=float, default=4.0)
    root.add_argument("--fps", type=int, default=25)
    root.add_argument("--width", type=int, default=1080)
    root.add_argument("--height", type=int, default=1920)
    root.add_argument("--text", default="")
    root.add_argument("--audio", type=Path)
    root.add_argument("--unit-id")
    root.add_argument("--speaking", choices=("true", "false"))
    root.add_argument(
        "--operation",
        choices=(
            "build_bible",
            "diagnose_episode",
            "develop_series",
            "review_series_development",
            "plan_showrunner",
            "plan_beat_script",
            "plan_beat_direction",
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
        elif args.operation == "develop_series":
            diagnoses = request["chapter_diagnoses"]
            payload = {
                "schema_version": 1,
                "development_version": request["development_version"],
                "novel_title": request["novel_title"],
                "engine": {
                    "pressure_loop": "林晚越想控制门，门外压力越逼近",
                    "protagonist_default_strategy": "先封锁风险再寻找证据",
                    "strategy_creates_problem": "封锁让同伴更怀疑她隐瞒真相",
                    "escalation_ladder": ["异响", "质疑", "强行开门"],
                    "termination_condition": "林晚公开门外真相并承担后果",
                },
                "relationship_pressure_network": [],
                "obligations": [],
                "chapter_projections": [
                    {
                        "episode_index": index,
                        "source_chapter": diagnosis["source_chapter"],
                        "arc_position": f"压力阶梯第{index}步",
                        "pressure_step": diagnosis["core_event"],
                        "allowed_event_ids": [
                            event["event_id"] for event in diagnosis["events"]
                        ],
                        "allowed_reveal_event_ids": [
                            event["event_id"] for event in diagnosis["events"]
                        ],
                        "setup_obligation_ids": [],
                        "payoff_obligation_ids": [],
                        "required_close_state": diagnosis["chapter_end_state"],
                    }
                    for index, diagnosis in enumerate(diagnoses, 1)
                ],
            }
        elif args.operation == "review_series_development":
            payload = {
                "passed": True,
                "engine_coherent": True,
                "projections_grounded": True,
                "future_fact_leakage": False,
                "issues": [],
            }
        elif args.operation == "plan_showrunner":
            episode = request["episode"]
            source = episode["source_text"]
            beat_functions = ["hook", "question", "escalation", "payoff", "cliffhanger"]
            payload = {
                "planning_mode": "planner",
                "episode_mode": "pressure_episode",
                "protagonist_choice": "",
                "choice_source_quote": "",
                "cost_paid": "",
                "cost_source_quote": "",
                "opposition": {
                    "opponent_name": "门外压力",
                    "goal": "迫使林晚打开门",
                    "tactic": "持续制造异响",
                    "source_event_ids": ["event_001"],
                },
                "retention": {
                    "target_duration_seconds": 30,
                    "max_attention_gap_ratio": 0.25,
                    "beats": [
                        {
                            "beat_id": f"beat_{index:03d}",
                            "function": function,
                            "target_start_ratio": (index - 1) * 0.2,
                            "target_end_ratio": min(1, (index - 1) * 0.2 + 0.18),
                            "audience_question": "门外是谁？",
                            "promise": "当前章内揭示林晚为何阻止开门",
                            "new_information_fact_ids": ["fact_001"] if index == 1 else [],
                            "emotional_shift": "危险逐步逼近",
                            "event_ids": ["event_001"],
                            "shot_indexes": [],
                            "source_quote": source,
                        }
                        for index, function in enumerate(beat_functions, 1)
                    ],
                    "ending_open_loop": "门外人身份仍未揭晓",
                },
                "information_states": [
                    {
                        "fact_id": "fact_001",
                        "statement": "林晚阻止开门",
                        "truth_status": "confirmed",
                        "viewer_awareness": "knows",
                        "character_awareness": [
                            {
                                "character_name": "林晚",
                                "awareness": "knows",
                                "belief": "门外存在风险",
                            }
                        ],
                        "dramatic_use": "viewer_leads",
                        "source_event_ids": ["event_001"],
                        "source_quote": source,
                        "reveal_beat_id": "beat_001",
                    }
                ],
                "character_state_deltas": [],
            }
        elif args.operation == "plan_beat_script":
            episode = request["episode"]
            source = episode["source_text"]
            beat = request["retention_beat"]
            dialogue_match = re.search(r"[“\"]([^”\"]+)[”\"]", source)
            dialogue = dialogue_match.group(1) if dialogue_match else "不要开门。"
            native = bool(request["requirements"].get("native_dialogue"))
            function_alias = {
                "hook": "establish",
                "question": "withhold",
                "escalation": "pressure",
                "reversal": "reveal",
            }
            dramatic_function = function_alias.get(beat["function"], beat["function"])
            rows = (
                [
                    ("门外有声音。", "林晚", "林晚", True, "derived"),
                    (dialogue, "林晚", "林晚", True, "verbatim"),
                    ("别靠近门。", "林晚", "林晚", True, "derived"),
                ]
                if native
                else [
                    ("门外传来异响。", "narrator", "旁白", False, "derived"),
                    (dialogue, "林晚", "林晚", True, "verbatim"),
                    ("林晚挡在门前。", "narrator", "旁白", False, "derived"),
                ]
            )
            payload = {
                "beat_id": beat["beat_id"],
                "open_state": request["incoming_close_state"],
                "close_state": f"{beat['beat_id']}的压力已落到门前",
                "released_fact_ids": beat["new_information_fact_ids"],
                "shots": [
                    {
                        "local_index": index,
                        "scene_job": f"{beat['function']}推进",
                        "change": f"观众看到{beat['beat_id']}第{index}步发生",
                        "blocking": text,
                        "characters": ["林晚"],
                        "location": "门外",
                        "source_quote": source,
                        "event_ids": beat["event_ids"],
                        "shot_intent": {
                            "dramatic_function": dramatic_function,
                            "power_relation": "林晚压住开门冲动",
                            "emotion_target": "危险逼近",
                            "information_fact_ids": beat["new_information_fact_ids"],
                            "viewer_focus": text,
                            "retention_beat_id": beat["beat_id"],
                        },
                        "turns": [
                            {
                                "role": role,
                                "speaker_name": speaker,
                                "text": text,
                                "speaking": speaking,
                                "delivery_mode": (
                                    "visible_dialogue" if speaking else "narration"
                                ),
                                "source_quote": source,
                                "derivation": derivation,
                                **(
                                    {
                                        "device": (
                                            "listener_qa"
                                            if native
                                            else "narration"
                                        )
                                    }
                                    if derivation == "derived"
                                    else {}
                                ),
                                **(
                                    {"serves": beat["event_ids"]}
                                    if derivation == "derived"
                                    else {}
                                ),
                            }
                        ],
                    }
                    for index, (text, role, speaker, speaking, derivation) in enumerate(rows, 1)
                ],
            }
        elif args.operation == "plan_beat_direction":
            beat = request["retention_beat"]
            script = request["accepted_script"]
            native = bool(request["requirements"].get("native_dialogue"))
            payload = {
                "beat_id": beat["beat_id"],
                "shots": [
                    {
                        "source_shot_index": shot["local_index"],
                        "turn_start": 1,
                        "turn_end": len(shot["turns"]),
                        "shot_scale": "中近景",
                        "visual_prompt": shot["blocking"],
                        "motion_prompt": "林晚先看向门，再抬手挡住",
                        "performance_plan": {
                            "objective": shot["change"],
                            "start_state": "林晚尚未转向门口",
                            "motion_beats": [
                                {
                                    "phase": "development",
                                    "seconds": 1.0,
                                    "actor": "林晚",
                                    "target": "木门",
                                    "action_type": "confront",
                                    "trigger": "门外异响",
                                    "action": "林晚看向门并抬手挡住",
                                    "reaction": "身体重心移向门口",
                                    "end_state": "林晚停在门前",
                                }
                            ],
                            "end_state": "林晚停在门前",
                        },
                        "camera_plan": {
                            "mode": "motivated_subtle",
                            "motivation": "随林晚挡门收紧关系",
                            "action_axis": "门与林晚之间的行动轴同侧",
                            "screen_direction": "林晚保持画面左侧并看向右侧门口",
                            "start_position": "门内中景",
                            "camera_beats": [
                                {
                                    "phase": "development",
                                    "trajectory": "短距离慢推一次",
                                    "framing": "从中景收至胸像",
                                    "parallax": "门框快于远墙移动",
                                }
                            ],
                            "end_position": "行动轴同侧胸像",
                        },
                        "visual_strategy": "direct-assets",
                        "keyframe_reasons": [],
                        "script_open_state": {
                            "knowledge": {"林晚": "知道门外有声音"},
                            "power": {"林晚": "控制门口"},
                            "relationship": {"林晚-门外者": "对立"},
                            "physical": {"林晚": "站在门前"},
                            "ongoing_action": "none",
                        },
                        "script_close_state": {
                            "knowledge": {"林晚": "知道门外有声音"},
                            "power": {"林晚": "控制门口"},
                            "relationship": {"林晚-门外者": "对立"},
                            "physical": {"林晚": "站在门前"},
                            "ongoing_action": "none",
                        },
                        "audio_plan": {
                            "speech_strategy": "native" if native else "locked",
                            "ambience": "门外低声风响",
                            "audio_beats": [
                                {
                                    "position_ratio": 0.2,
                                    "cue_type": "ambience",
                                    "cue": "门外异响",
                                    "trigger": "林晚看门",
                                    "retention_beat_id": beat["beat_id"],
                                }
                            ],
                        },
                    }
                    for shot in script["shots"]
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
            dialogue_match = re.search(r"[“\"]([^”\"]+)[”\"]", source)
            dialogue = dialogue_match.group(1) if dialogue_match else "不要开门。"
            turns = [
                {
                    "role": "林晚",
                    "speaker_name": "林晚",
                    "text": dialogue,
                    "speaking": True,
                    "source_quote": source,
                }
            ]
            if request.get("creative_profile") != "short-drama-adaptive-v1":
                turns.append(
                    {
                        "role": "narrator",
                        "speaker_name": "旁白",
                        "text": "她挡在门前。",
                        "speaking": False,
                        "source_quote": source,
                    }
                )
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
                        "turns": turns,
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
                "-f", "lavfi", "-i", f"sine=frequency=330:duration={args.duration:.6f}",
                "-t", f"{args.duration:.6f}", "-vf", f"scale={args.width}:{args.height},format=yuv420p",
                "-r", str(args.fps), "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac", "-shortest", str(args.output),
            ],
            check=True,
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
