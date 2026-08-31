from __future__ import annotations

import re

from .models import EpisodeMode, EpisodePlan, TurnDelivery, TurnDevice
from .production_models import ProductionPlan


def _spoken_chars(value: str) -> int:
    return len(re.sub(r"[\s，。！？；：、…,.!?;:\"“”'‘’（）()]", "", value))


def _expected_unit_seconds(unit) -> float:
    dialogue_seconds = (
        0.0
        if unit.delivery_mode
        in {TurnDelivery.TITLE_CARD, TurnDelivery.SILENT_ACTION}
        else _spoken_chars(unit.text) / 4.0 + 1.0
    )
    beat_seconds = (
        sum(float(beat.seconds or 0.0) for beat in unit.performance_plan.motion_beats)
        if unit.performance_plan is not None
        else 0.0
    )
    return round(min(14.0, max(4.0, dialogue_seconds + beat_seconds)), 3)


def evaluate_production_preflight(
    episode_plan: EpisodePlan,
    production_plan: ProductionPlan,
    *,
    native_dialogue: bool = False,
) -> dict:
    """Run deterministic PRE checks before any episode media is generated."""

    if episode_plan.episode_contract is None:
        return {
            "schema_version": 1,
            "status": "not_applicable_legacy_plan",
            "passed": True,
            "issues": [],
            "vlm_questions": [],
        }
    issues = []
    units_by_id = {unit.unit_id: unit for unit in production_plan.units}
    prior_close = None
    prior_index = None
    for shot in production_plan.shots:
        units = [units_by_id[unit_id] for unit_id in shot.unit_ids]
        open_state = units[0].script_open_state
        close_state = units[-1].script_close_state
        if open_state is None or close_state is None:
            issues.append(
                {
                    "code": "handoff_state_missing",
                    "shot_id": shot.shot_id,
                    "detail": "v5 shots require structured open and close handoff states",
                }
            )
        if prior_close is not None and open_state is not None:
            mismatches = [
                field
                for field in (
                    "knowledge",
                    "power",
                    "relationship",
                    "physical",
                    "ongoing_action",
                )
                if getattr(prior_close, field) != getattr(open_state, field)
            ]
            if mismatches:
                issues.append(
                    {
                        "code": "handoff_state_mismatch",
                        "shot_id": shot.shot_id,
                        "previous_shot_index": prior_index,
                        "dimensions": mismatches,
                    }
                )
        prior_close = close_state
        prior_index = shot.index
        beat_indexes = [
            index
            for unit in units
            for index in unit.performance_beat_indexes
        ]
        if beat_indexes:
            if len(beat_indexes) != len(set(beat_indexes)):
                issues.append(
                    {
                        "code": "motion_beat_assigned_more_than_once",
                        "shot_id": shot.shot_id,
                    }
                )
            if sorted(beat_indexes) != list(range(max(beat_indexes) + 1)):
                issues.append(
                    {
                        "code": "motion_beat_assignment_gap",
                        "shot_id": shot.shot_id,
                        "indexes": beat_indexes,
                    }
                )
        for unit in units:
            expected = _expected_unit_seconds(unit)
            if abs(unit.planned_seconds - expected) > 0.011:
                issues.append(
                    {
                        "code": "unit_planned_duration_inconsistent",
                        "unit_id": unit.unit_id,
                        "planned_seconds": unit.planned_seconds,
                        "expected_seconds": expected,
                    }
                )
            if native_dialogue and (
                unit.delivery_mode
                in {TurnDelivery.NARRATION, TurnDelivery.INNER_VOICE}
                or unit.role == "narrator"
                and unit.delivery_mode != TurnDelivery.TITLE_CARD
            ):
                issues.append(
                    {
                        "code": "native_dialogue_voiceover_present",
                        "unit_id": unit.unit_id,
                    }
                )
            source_shot = episode_plan.shots[unit.shot_index - 1]
            source_turn = source_shot.turns[unit.turn_index - 1]
            if native_dialogue and source_turn.device in {
                TurnDevice.NARRATION,
                TurnDevice.INNER_VOICE,
            }:
                issues.append(
                    {
                        "code": "native_dialogue_voiceover_device_present",
                        "unit_id": unit.unit_id,
                    }
                )
    for group in production_plan.visual_groups:
        units = [units_by_id[unit_id] for unit_id in group.unit_ids]
        expected = round(
            min(
                14.0,
                max(
                    4.0,
                    sum(unit.planned_seconds for unit in units)
                    + 0.1 * max(0, len(units) - 1),
                ),
            ),
            3,
        )
        if abs(group.planned_seconds - expected) > 0.011:
            issues.append(
                {
                    "code": "group_planned_duration_inconsistent",
                    "group_id": group.group_id,
                    "planned_seconds": group.planned_seconds,
                    "expected_seconds": expected,
                }
            )
        if (
            group.shot_contract is None
            or abs(group.shot_contract.duration_seconds - group.planned_seconds) > 0.011
        ):
            issues.append(
                {
                    "code": "shot_contract_duration_inconsistent",
                    "group_id": group.group_id,
                }
            )
        if (
            native_dialogue
            and group.shot_contract is not None
            and group.shot_contract.external_audio_is_master
        ):
            issues.append(
                {
                    "code": "native_dialogue_external_audio_master",
                    "group_id": group.group_id,
                }
            )
        if group.shot_contract is not None and (
            group.shot_contract.open_handoff is None
            or group.shot_contract.close_handoff is None
        ):
            issues.append(
                {
                    "code": "shot_contract_handoff_missing",
                    "group_id": group.group_id,
                }
            )
    vlm_questions = []
    for group in production_plan.visual_groups:
        contract = group.shot_contract
        if contract is None:
            continue
        for index, change in enumerate(contract.changes_here, start=1):
            vlm_questions.append(
                {
                    "question_id": f"{group.group_id}_change_{index:02d}",
                    "group_id": group.group_id,
                    "question": f"镜头是否清楚、可见地呈现：{change}？",
                    "expected": "yes",
                    "source": "shot_contract.changes_here",
                }
            )
        vlm_questions.append(
            {
                "question_id": f"{group.group_id}_subjects",
                "group_id": group.group_id,
                "question": (
                    "画面可见具名主体是否严格匹配合同中的"
                    f"{len(contract.visible_asset_ids)}个资产，且没有新增人物？"
                ),
                "expected": "yes",
                "source": "shot_contract.visible_asset_ids",
            }
        )
    dramaturgy = episode_plan.dramaturgy
    if dramaturgy is not None and dramaturgy.opposition is not None:
        vlm_questions.append(
            {
                "question_id": "episode_opposition_visible",
                "group_id": None,
                "question": (
                    f"是否能看见{dramaturgy.opposition.opponent_name}通过"
                    f"{dramaturgy.opposition.tactic}向主角施压？"
                ),
                "expected": "yes",
                "source": "episode_dramaturgy.opposition",
            }
        )
    if dramaturgy is not None and dramaturgy.episode_mode == EpisodeMode.CHOICE:
        vlm_questions.append(
            {
                "question_id": "episode_choice_and_cost_visible",
                "group_id": None,
                "question": (
                    f"主角的选择“{dramaturgy.protagonist_choice}”及已付代价"
                    f"“{dramaturgy.cost_paid}”是否都在画面或原生对白中兑现？"
                ),
                "expected": "yes",
                "source": "episode_dramaturgy.choice",
            }
        )
    return {
        "schema_version": 1,
        "status": "passed" if not issues else "failed",
        "passed": not issues,
        "policy": "planner-v5-structured-preflight-v1",
        "issues": issues,
        "vlm_questions": vlm_questions,
        "summary": {
            "shot_count": len(production_plan.shots),
            "unit_count": len(production_plan.units),
            "group_count": len(production_plan.visual_groups),
            "vlm_question_count": len(vlm_questions),
        },
    }
