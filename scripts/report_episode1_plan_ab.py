#!/usr/bin/env python3
"""Build the reproducible EP1 planning A/B metrics and blind-review packet."""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path


SILENT_DELIVERIES = {"silent_action", "title_card"}
ACTIVE_ACTION_TYPES = {
    "choose",
    "refuse",
    "confront",
    "ask",
    "move",
    "reveal",
    "press",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-plan", type=Path, required=True)
    parser.add_argument("--new-plan", type=Path, required=True)
    parser.add_argument("--ceiling-plan", type=Path, required=True)
    parser.add_argument("--new-quality", type=Path, required=True)
    parser.add_argument("--new-evidence-root", type=Path, required=True)
    parser.add_argument("--cold-failure", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def compact_chars(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def plan_metrics(path: Path) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8"))
    shots = plan["shots"]
    turns = [turn for shot in shots for turn in shot.get("turns", [])]
    audible = [
        turn
        for turn in turns
        if turn.get("delivery_mode") not in SILENT_DELIVERIES
    ]
    visible = [
        turn for turn in turns if turn.get("delivery_mode") == "visible_dialogue"
    ]
    offscreen = [
        turn for turn in turns if turn.get("delivery_mode") == "offscreen_dialogue"
    ]
    voiceover = [
        turn
        for turn in turns
        if turn.get("delivery_mode") in {"narration", "inner_voice"}
    ]
    derived = [
        turn for turn in turns if turn.get("derivation", "verbatim") == "derived"
    ]
    audible_derived = [turn for turn in audible if turn in derived]
    anchored = [
        turn
        for turn in audible
        if turn.get("derivation", "verbatim") in {"verbatim", "abridged"}
    ]
    active_shots = []
    for shot in shots:
        beats = (shot.get("performance_plan") or {}).get("motion_beats", [])
        if any(beat.get("action_type") in ACTIVE_ACTION_TYPES for beat in beats):
            active_shots.append(shot["index"])
    return {
        "path": str(path.resolve()),
        "shots": len(shots),
        "semantic_turns": len(turns),
        "audible_turns": len(audible),
        "visible_dialogue_turns": len(visible),
        "offscreen_dialogue_turns": len(offscreen),
        "silent_action_turns": sum(
            turn.get("delivery_mode") == "silent_action" for turn in turns
        ),
        "title_card_turns": sum(
            turn.get("delivery_mode") == "title_card" for turn in turns
        ),
        "all_turn_chars": sum(compact_chars(turn.get("text", "")) for turn in turns),
        "audible_chars": sum(compact_chars(turn.get("text", "")) for turn in audible),
        "visible_dialogue_chars": sum(
            compact_chars(turn.get("text", "")) for turn in visible
        ),
        "offscreen_dialogue_chars": sum(
            compact_chars(turn.get("text", "")) for turn in offscreen
        ),
        "voiceover_chars": sum(
            compact_chars(turn.get("text", "")) for turn in voiceover
        ),
        "max_audible_turn_chars": max(
            (compact_chars(turn.get("text", "")) for turn in audible),
            default=0,
        ),
        "verbatim_turns": sum(
            turn.get("derivation", "verbatim") == "verbatim" for turn in turns
        ),
        "abridged_turns": sum(
            turn.get("derivation") == "abridged" for turn in turns
        ),
        "derived_turns": len(derived),
        "audible_derived_turns": len(audible_derived),
        "audible_source_anchored_turn_ratio": (
            round(len(anchored) / len(audible), 6) if audible else 0.0
        ),
        "derived_serves_coverage": (
            round(sum(bool(turn.get("serves")) for turn in derived) / len(derived), 6)
            if derived
            else 1.0
        ),
        "active_action_shots": len(active_shots),
        "multi_character_shots": sum(
            len(set(shot.get("characters", []))) >= 2 for shot in shots
        ),
        "empty_character_shots": sum(not shot.get("characters") for shot in shots),
        "empty_change_shots": sum(not shot.get("change", "").strip() for shot in shots),
    }


def call_counts(root: Path) -> dict:
    patterns = {
        "series_development": "series_development/series_development.v*.attempt_*.raw.json",
        "series_review": "series_development/series_development_review.v*.attempt_*.raw.json",
        "showrunner": "script_drafts/episode_001/showrunner_attempt_*.raw.json",
        "beat_script": "script_drafts/episode_001/beats/*/script_attempt_*.json",
        "beat_direction": "script_drafts/episode_001/beats/*/direction_attempt_*.json",
        "episode_review": "script_drafts/episode_001/review_attempt_*.raw.json",
        "series_state": "script_drafts/episode_001/series_state_attempt_*.raw.json",
    }
    counts = {name: len(list(root.glob(pattern))) for name, pattern in patterns.items()}
    counts["chapter_diagnosis_inferred_calls"] = 1
    counts["total_recorded_model_calls"] = sum(counts.values())
    return counts


def blind_view(path: Path) -> list[dict]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "shot": shot["index"],
            "change": shot.get("change", ""),
            "event_ids": shot.get("event_ids", []),
            "characters": shot.get("characters", []),
            "turns": [
                {
                    "speaker": turn.get("speaker_name", ""),
                    "delivery": turn.get("delivery_mode", ""),
                    "text": turn.get("text", ""),
                }
                for turn in shot.get("turns", [])
            ],
            "action": (shot.get("motion_prompt") or "")[:220],
        }
        for shot in plan["shots"]
    ]


def main() -> int:
    args = parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    old = plan_metrics(args.old_plan)
    new = plan_metrics(args.new_plan)
    ceiling = plan_metrics(args.ceiling_plan)
    quality = json.loads(args.new_quality.read_text(encoding="utf-8"))
    cold_failure = json.loads(args.cold_failure.read_text(encoding="utf-8"))
    metrics = {
        "comparison_contract": {
            "source": "同一第一章",
            "generator_model": "DeepSeek-V4-Flash-0731 via deepseek-local",
            "old_provenance": "pinned commit ea29f47 + existing draft; not rerun",
            "new_provenance": "v5 successful resumed planning sample; no media",
            "ceiling_provenance": "hand-written ceiling reference; not a model arm",
            "density_is_report_only": True,
        },
        "old_deepseek": old,
        "v5_deepseek": new,
        "handwritten_ceiling": ceiling,
        "v5_quality": quality,
        "v5_recorded_calls": call_counts(args.new_evidence_root),
        "clean_cold_attempt": {
            "passed": False,
            "elapsed_seconds": cold_failure.get("elapsed_seconds"),
            "failed_stage": "plan_showrunner",
            "error": cold_failure.get("error"),
        },
    }
    (output / "plan_ab_metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    labels = ["A", "B"]
    random.Random(20260831).shuffle(labels)
    mapping = {labels[0]: "old_deepseek", labels[1]: "v5_deepseek"}
    plans = {
        labels[0]: blind_view(args.old_plan),
        labels[1]: blind_view(args.new_plan),
    }
    blind_request = {
        "contract": "ep1-plan-blind-review/v1",
        "reviewer_model": "deepseek-local",
        "instructions": (
            "盲评两个同源第一集剧本。不要猜版本，不按镜数或字数直接判优；"
            "逐题给A/B/tie与可核对理由，最后给总胜者。只输出schema JSON。"
        ),
        "questions": [
            {"id": "q1", "text": "非原著观众能否在结果前后理解主角原有地位、当前落差与核心问题？"},
            {"id": "q2", "text": "每个主要beat是否通过选择、压力、揭示或后果改变局面，而非只复述事件？"},
            {"id": "q3", "text": "因果、信息释放和人物动机是否在行动前成立，且无后文泄漏？"},
            {"id": "q4", "text": "主角是否有可见主动动作或有代价的应对；pressure episode是否有明确压力主体？"},
            {"id": "q5", "text": "叙述性信息是否被动作、载体对白、反应或证据物外化，且删除测试能成立？"},
            {"id": "q6", "text": "对白是否短、可演、存在来回与反应，避免书面说教和单人长口播？"},
            {"id": "q7", "text": "结尾是否在当前章边界形成可见决定、后果或开放问题，产生下一集动力？"},
        ],
        "plans": plans,
        "schema": {
            "type": "object",
            "required": ["questions", "overall_winner", "summary"],
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 7,
                    "maxItems": 7,
                    "items": {
                        "type": "object",
                        "required": ["id", "winner", "reason"],
                        "properties": {
                            "id": {"type": "string"},
                            "winner": {"enum": ["A", "B", "tie"]},
                            "reason": {"type": "string", "maxLength": 500},
                        },
                    },
                },
                "overall_winner": {"enum": ["A", "B", "tie"]},
                "summary": {"type": "string", "maxLength": 1000},
                "risks": {"type": "array", "items": {"type": "string"}},
            },
        },
    }
    (output / "blind_review_request.json").write_text(
        json.dumps(blind_request, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output / "blind_review_mapping.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"metrics": str(output / "plan_ab_metrics.json"), "mapping": mapping}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
