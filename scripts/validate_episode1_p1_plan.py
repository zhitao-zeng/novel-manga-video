#!/usr/bin/env python3
"""Validate the episode-1 P1 hybrid plan without calling media providers."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from novel_manga.models import EpisodePlan, VisualStrategy
from novel_manga.production import visible_character_names_for_shot
from novel_manga.production_models import ProductionPlan, SeriesAssetManifest
from novel_manga.util import atomic_write_json


SILENT = "【无对白动作镜】"
KEYFRAME_TOKENS = (
    "按碑",
    "触摸",
    "按住",
    "行礼",
    "挡住",
    "绕到",
    "追上",
    "并肩",
)
REQUIRED_PROMPT_FIELDS = (
    "表演：",
    "情绪：",
    "权力关系：",
    "观众焦点：",
    "摄影机：",
    "SD2.5原声：",
)
TEMPLATE_LEAKS = ("桌沿", "书页", "固定窗")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--asset-manifest", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def normalized(value: str) -> str:
    return "".join(value.split())


def main() -> int:
    args = parse_args()
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
    assets = SeriesAssetManifest.model_validate_json(
        args.asset_manifest.resolve().read_text(encoding="utf-8")
    )
    trace = json.loads(
        (episode_dir / "content_trace.json").read_text(encoding="utf-8")
    )
    source = args.source.resolve().read_text(encoding="utf-8")
    source_normalized = normalized(source)

    failures: list[str] = []
    rows = manifest["groups"]
    production_groups = {row.group_id: row for row in production.visual_groups}
    shots = {f"shot_{shot.index:03d}": shot for shot in episode.shots}
    asset_id_by_name = {row.name: row.asset_id for row in assets.characters}
    version_by_id = {
        row.asset_id: row.version for row in [*assets.characters, *assets.locations]
    }

    if len(rows) != 20 or len(production_groups) != 20:
        failures.append("expected exactly 20 physical visual groups")
    if manifest.get("plan_mode") != "hybrid":
        failures.append("plan_mode is not hybrid")
    if manifest.get("qwen_image_used") is not False:
        failures.append("Qwen Image must remain disabled")
    if manifest.get("minimax_h3_used") is not False:
        failures.append("MiniMax H3 must remain disabled")
    if manifest.get("final_audio_policy") != "sd25_native_original":
        failures.append("final audio policy is not SD2.5 native original")
    if manifest.get("external_audio_is_master") is not False:
        failures.append("external audio is still marked master")

    missing_references: list[str] = []
    routing_mismatches: list[str] = []
    contract_mismatches: list[str] = []
    prompt_adapter_missing: list[str] = []
    prompt_field_missing: list[str] = []
    template_leak_groups: list[str] = []
    action_contract_mismatches: list[str] = []
    keyframe_groups: list[str] = []
    direct_groups: list[str] = []
    actions_by_shot: dict[str, list[str]] = defaultdict(list)

    for row in rows:
        group_id = row["group_id"]
        shot_id = row["shot_ids"][0]
        shot = shots[shot_id]
        group = production_groups[group_id]
        expected_visible_names = visible_character_names_for_shot(
            shot,
            tuple(asset_id_by_name),
        )
        expected_character_ids = [
            asset_id_by_name[name]
            for name in expected_visible_names
            if name in asset_id_by_name
        ]
        expected_character_ids = list(dict.fromkeys(expected_character_ids))
        reference_character_ids = [
            reference["asset_id"]
            for reference in row["references"]
            if reference["role"] == "character_identity_costume"
        ]
        if set(reference_character_ids) != set(expected_character_ids):
            missing_references.append(group_id)

        text = f"{shot.visual_prompt} {shot.motion_prompt}"
        expected_keyframe = len(expected_character_ids) >= 2 or any(
            token in text for token in KEYFRAME_TOKENS
        )
        if bool(row["keyframe_generation"]) != expected_keyframe:
            routing_mismatches.append(group_id)
        if expected_keyframe:
            keyframe_groups.append(group_id)
            if row["prompt_adapter"]["image_model"] != "gpt-image-2":
                routing_mismatches.append(f"{group_id}:image-model")
            if row["prompt_adapter"]["image_prompt"].startswith("disabled"):
                routing_mismatches.append(f"{group_id}:disabled-image-prompt")
            if row.get("keyframe_reason") not in {
                "multi_character_blocking",
                "critical_prop_or_blocking_interaction",
            }:
                routing_mismatches.append(f"{group_id}:reason")
            if group.visual_strategy != VisualStrategy.STORY_KEYFRAME:
                routing_mismatches.append(f"{group_id}:production-strategy")
        else:
            direct_groups.append(group_id)
            if row["prompt_adapter"]["image_prompt"] != "disabled:no-keyframe":
                routing_mismatches.append(f"{group_id}:unexpected-image-prompt")
            if row["prompt_adapter"]["image_model"] != "disabled-no-keyframe":
                routing_mismatches.append(f"{group_id}:unexpected-image-model")
            if group.visual_strategy != VisualStrategy.DIRECT_ASSETS:
                routing_mismatches.append(f"{group_id}:production-strategy")

        expected_version_ids = {
            f"{asset_id}@{version_by_id[asset_id]}"
            for asset_id in expected_character_ids
        }
        image_contract = group.image_contract
        shot_contract = group.shot_contract
        if image_contract is None or shot_contract is None:
            contract_mismatches.append(f"{group_id}:missing-contract")
        else:
            if image_contract.exact_subject_count != len(expected_character_ids):
                contract_mismatches.append(f"{group_id}:subject-count")
            if set(image_contract.subject_asset_version_ids) != expected_version_ids:
                contract_mismatches.append(f"{group_id}:image-subjects")
            if set(shot_contract.visible_asset_ids) != expected_version_ids:
                contract_mismatches.append(f"{group_id}:visible-subjects")
            if shot_contract.external_audio_is_master:
                contract_mismatches.append(f"{group_id}:external-audio")
            manifest_actions = [
                str(value) for value in row["performance_contract"]["actions"]
            ]
            contract_actions = [beat.action for beat in shot_contract.beat_timeline]
            if contract_actions != manifest_actions:
                action_contract_mismatches.append(group_id)
            actions_by_shot[shot_id].extend(
                action for action in manifest_actions if "不重复前组走位" not in action
            )

        adapter = row.get("prompt_adapter")
        if not adapter:
            prompt_adapter_missing.append(group_id)
        else:
            for marker in REQUIRED_PROMPT_FIELDS:
                if marker not in adapter["video_prompt"]:
                    prompt_field_missing.append(f"{group_id}:{marker}")
        if any(token in row["prompt"] for token in TEMPLATE_LEAKS):
            template_leak_groups.append(group_id)
        if row["audio_plan"]["speech_strategy"] != "native":
            contract_mismatches.append(f"{group_id}:speech-strategy")
        if row.get("audio_path") is not None or row.get("video_audio_path") is not None:
            contract_mismatches.append(f"{group_id}:external-audio-path")

    duplicate_story_actions = {
        shot_id: actions
        for shot_id, actions in actions_by_shot.items()
        if len(actions) != len(set(actions))
    }
    silent_durations = [
        float(row["delivery_duration"]) for row in rows if row["silent"]
    ]
    if not all(1.5 <= seconds <= 2.5 for seconds in silent_durations):
        failures.append("silent delivery durations are outside 1.5-2.5 seconds")

    shot_016_units = [
        unit for unit in production.units if unit.shot_id == "shot_016"
    ]
    shot_016_physics_clear = all(
        unit.action_physics_plan is None for unit in shot_016_units
    )
    if not shot_016_physics_clear:
        failures.append("shot_016 still has hallucinated battle-energy physics")
    if any(unit.audio_plan.speech_strategy != "native" for unit in production.units):
        failures.append("production units still request locked speech")
    if any(
        group.shot_contract is None
        or group.shot_contract.external_audio_is_master
        for group in production.visual_groups
    ):
        failures.append("production shot contracts still use external audio master")

    trace_quotes = [
        quote
        for shot in trace.get("shots", [])
        for quote in [shot.get("source_quote", "")]
        if quote
    ] + [
        turn.get("source_quote", "")
        for shot in trace.get("shots", [])
        for turn in shot.get("turns", [])
        if turn.get("source_quote")
    ]
    trace_grounded = (
        len(trace.get("shots", [])) == 16
        and all(normalized(quote) in source_normalized for quote in trace_quotes)
    )
    if not trace_grounded:
        failures.append("content trace is incomplete or contains ungrounded quotes")
    source_hash_matches = (
        trace.get("source_text_sha256") == manifest.get("source_text_sha256")
    )
    if not source_hash_matches:
        failures.append("content trace source hash mismatch")

    task_sidecars = [
        str(path.relative_to(episode_dir))
        for path in episode_dir.rglob("*task*.json")
    ]
    if task_sidecars:
        failures.append("new provider task sidecars were created")

    for values in (
        missing_references,
        routing_mismatches,
        contract_mismatches,
        prompt_adapter_missing,
        prompt_field_missing,
        template_leak_groups,
        action_contract_mismatches,
    ):
        if values:
            failures.extend(values)
    if duplicate_story_actions:
        failures.append(f"duplicate story actions: {duplicate_story_actions}")

    report = {
        "schema_version": 1,
        "status": "passed" if not failures else "failed",
        "episode_dir": str(episode_dir),
        "group_count": len(rows),
        "keyframe_group_count": len(keyframe_groups),
        "direct_group_count": len(direct_groups),
        "keyframe_groups": keyframe_groups,
        "direct_groups": direct_groups,
        "keyframe_image_model": manifest.get("keyframe_image_model"),
        "qwen_image_used": manifest.get("qwen_image_used"),
        "minimax_h3_used": manifest.get("minimax_h3_used"),
        "missing_visible_character_references": missing_references,
        "routing_mismatches": routing_mismatches,
        "contract_mismatches": contract_mismatches,
        "prompt_adapter_nonnull": len(rows) - len(prompt_adapter_missing),
        "prompt_field_missing": prompt_field_missing,
        "template_leak_groups": template_leak_groups,
        "action_contract_mismatches": action_contract_mismatches,
        "duplicate_story_actions": duplicate_story_actions,
        "shot_016_physics_clear": shot_016_physics_clear,
        "silent_delivery_durations": silent_durations,
        "final_audio_policy": manifest.get("final_audio_policy"),
        "external_audio_is_master": manifest.get("external_audio_is_master"),
        "content_trace_grounded": trace_grounded,
        "content_trace_source_hash_matches": source_hash_matches,
        "new_provider_task_sidecars": task_sidecars,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
