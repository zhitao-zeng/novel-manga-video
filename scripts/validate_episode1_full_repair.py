#!/usr/bin/env python3
"""Produce the local R1-R10 repair ledger before remote media submission."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from novel_manga.models import EpisodePlan
from novel_manga.production_models import ProductionPlan
from novel_manga.util import atomic_write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--plan-validation", type=Path, required=True)
    parser.add_argument("--old-bad-qc", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    episode_dir = args.episode_dir.resolve()
    manifest = json.loads(
        (episode_dir / "sd25_direct_plan.json").read_text(encoding="utf-8")
    )
    episode = EpisodePlan.model_validate_json(
        (episode_dir / "episode_plan.json").read_text(encoding="utf-8")
    )
    production = ProductionPlan.model_validate_json(
        (episode_dir / "production_plan_sd25.json").read_text(encoding="utf-8")
    )
    metrics = json.loads(
        (episode_dir / "script_metrics.json").read_text(encoding="utf-8")
    )
    plan_validation = json.loads(
        args.plan_validation.resolve().read_text(encoding="utf-8")
    )
    old_bad_qc = json.loads(args.old_bad_qc.resolve().read_text(encoding="utf-8"))
    preflight = json.loads(
        (episode_dir / "hybrid_native_execution_preflight.json").read_text(
            encoding="utf-8"
        )
    )

    rows = manifest["groups"]
    groups = {group.group_id: group for group in production.visual_groups}
    keyframe_rows = [row for row in rows if row["keyframe_generation"]]
    direct_rows = [row for row in rows if not row["keyframe_generation"]]
    prompt_markers = (
        "表演：",
        "情绪：",
        "权力关系：",
        "观众焦点：",
        "摄影机：",
        "SD2.5原声：",
    )
    prompt_field_coverage = {
        marker: sum(marker in row["prompt"] for row in rows)
        for marker in prompt_markers
    }
    template_leaks = [
        row["group_id"]
        for row in rows
        if any(token in row["prompt"] for token in ("桌沿", "书页", "固定窗"))
    ]
    shot16_physics = [
        unit.unit_id
        for unit in production.units
        if unit.shot_id == "shot_016" and unit.action_physics_plan is not None
    ]
    camera_modes = [shot.camera_plan.mode for shot in episode.shots if shot.camera_plan]
    locked_ratio = camera_modes.count("locked") / len(camera_modes)
    emphasis_ratio = camera_modes.count("motivated_emphasis") / len(camera_modes)
    shot10_order = [
        row["unit_ids"] for row in rows if row["shot_ids"] == ["shot_010"]
    ]
    shot15_order = [
        row["unit_ids"] for row in rows if row["shot_ids"] == ["shot_015"]
    ]
    contract_action_mismatches = [
        row["group_id"]
        for row in rows
        if [
            beat.action
            for beat in groups[row["group_id"]].shot_contract.beat_timeline
        ]
        != [str(value) for value in row["performance_contract"]["actions"]]
    ]
    new_task_sidecars = [
        str(path.relative_to(episode_dir))
        for path in episode_dir.rglob("*.task.json")
    ]

    roots = {
        "R1": {
            "passed": (
                len(keyframe_rows) == 15
                and len(direct_rows) == 5
                and manifest["keyframe_image_model"] == "gpt-image-2"
                and manifest["qwen_image_used"] is False
                and manifest["minimax_h3_used"] is False
                and preflight["reference_audio_used"] is False
            ),
            "evidence": {
                "keyframe_groups": len(keyframe_rows),
                "direct_groups": len(direct_rows),
                "image_model": manifest["keyframe_image_model"],
            },
        },
        "R2": {
            "passed": all(value == 20 for value in prompt_field_coverage.values()),
            "evidence": prompt_field_coverage,
        },
        "R3": {
            "passed": (
                manifest["final_audio_policy"] == "sd25_native_original"
                and manifest["external_audio_is_master"] is False
                and all(row["audio_plan"]["speech_strategy"] == "native" for row in rows)
                and all("SD2.5原声：" in row["prompt"] for row in rows)
            ),
            "evidence": {
                "final_audio_policy": manifest["final_audio_policy"],
                "external_audio_is_master": manifest["external_audio_is_master"],
                "tts_paths_present": sum(row["audio_path"] is not None for row in rows),
            },
        },
        "R4": {
            "passed": (
                not plan_validation["missing_visible_character_references"]
                and not plan_validation["contract_mismatches"]
            ),
            "evidence": {
                "missing_references": plan_validation[
                    "missing_visible_character_references"
                ],
                "contract_mismatches": plan_validation["contract_mismatches"],
            },
        },
        "R5": {
            "passed": (
                all(
                    1.5 <= float(row["delivery_duration"]) <= 2.5
                    for row in rows
                    if row["silent"]
                )
                and all(float(row["generation_duration"]) >= float(row["delivery_duration"]) for row in rows)
                and all(
                    abs(
                        groups[row["group_id"]].shot_contract.duration_seconds
                        - float(row["delivery_duration"])
                    )
                    < 0.001
                    for row in rows
                )
            ),
            "evidence": {
                "silent_delivery_durations": [
                    row["delivery_duration"] for row in rows if row["silent"]
                ],
                "outro_seconds": 0.0,
            },
        },
        "R6": {
            "passed": (
                not template_leaks
                and not shot16_physics
                and not contract_action_mismatches
                and not plan_validation["duplicate_story_actions"]
            ),
            "evidence": {
                "template_leaks": template_leaks,
                "shot16_physics": shot16_physics,
                "action_contract_mismatches": contract_action_mismatches,
                "duplicate_story_actions": plan_validation[
                    "duplicate_story_actions"
                ],
            },
        },
        "R7": {
            "passed": locked_ratio < 0.50 and emphasis_ratio <= 0.20,
            "evidence": {
                "camera_modes": camera_modes,
                "locked_ratio": round(locked_ratio, 6),
                "emphasis_ratio": round(emphasis_ratio, 6),
            },
        },
        "R8": {
            "passed": (
                shot10_order
                == [["shot_010_turn_01"], ["shot_010_turn_02"]]
                and shot15_order
                == [
                    ["shot_015_turn_01"],
                    ["shot_015_turn_02", "shot_015_turn_03"],
                ]
            ),
            "evidence": {
                "shot10_order": shot10_order,
                "shot15_order": shot15_order,
            },
        },
        "R9": {
            "passed": (
                old_bad_qc.get("passed") is False
                and old_bad_qc["checks"]["silence_ratio"]["passed"] is False
            ),
            "evidence": {
                "old_bad_clip_rejected": old_bad_qc.get("passed") is False,
                "old_bad_silence_ratio": old_bad_qc["checks"]["silence_ratio"][
                    "detail"
                ]["ratio"],
                "semantic_gate": "director/VLM action, exact cast, extra object, screen direction",
                "identity_score_policy": "monitoring-only",
            },
        },
        "R10": {
            "passed": (
                metrics["max_turn_char_count"] <= 20
                and metrics["verbatim_turn_ratio"] <= metrics[
                    "verbatim_turn_ratio_max"
                ]
                and metrics["meaningful_change_coverage"] == 1.0
                and metrics["protagonist_agency_shot_count"] >= 6
                and metrics["named_conflict_shot_count"] >= 4
                and metrics["visible_cliffhanger"] is True
                and (episode_dir / "content_trace.json").is_file()
            ),
            "evidence": metrics,
        },
    }
    failures = [root for root, value in roots.items() if not value["passed"]]
    if new_task_sidecars:
        failures.append("remote_tasks_submitted_before_local_gate")
    report = {
        "schema_version": 1,
        "status": "passed" if not failures else "failed",
        "scope": "local-code-and-plan-before-media",
        "roots": roots,
        "remote_provider_task_sidecars": new_task_sidecars,
        "failures": failures,
        "next_gate": "generate media, complete director/VLM report, run video-side and final media QC",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
