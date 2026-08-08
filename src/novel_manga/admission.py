from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from .av_quality import (
    STATUS_FAILED,
    STATUS_PASSED,
    ass_layout,
    evaluate_asr,
    evaluate_subtitle_burn_in,
)
from .config import Settings
from .production_models import ProductionPlan
from .sd_dialogue import PUNCTUATION


ADMISSION_POLICY_REVISION = "novel-manga-av-v1.5-no-lip-review"


def admission_backend_identity(settings: Settings) -> dict:
    def digest(value: str | None) -> str | None:
        return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None

    return {
        "align_command_sha256": digest(settings.align_command),
        "asr_command_sha256": digest(settings.asr_command),
    }


def evaluate_subtitle_structure(
    plan: ProductionPlan,
    events: list[dict],
    ass: Path,
    max_cps: float,
) -> dict:
    by_unit: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        by_unit[str(event["unit_id"])].append(event)
    errors = []
    reading_speed = []
    previous_end = -1.0
    for unit in plan.units:
        rows = by_unit.get(unit.unit_id, [])
        reconstructed = "".join(
            "".join(str(row["text"]).replace(r"\N", "").split()) for row in rows
        )
        if reconstructed != "".join(unit.text.split()):
            errors.append(f"{unit.unit_id}: subtitle text differs from locked turn")
        for row in rows:
            if str(row.get("role")) != unit.role:
                errors.append(f"{unit.unit_id}: subtitle role differs from locked turn")
            text = str(row["text"])
            if text.count(r"\N") > 1:
                errors.append(f"{unit.unit_id}: subtitle has more than two lines")
            if not any(character not in PUNCTUATION + r"\N" for character in text):
                errors.append(f"{unit.unit_id}: punctuation-only subtitle page")
            start, end = float(row["start"]), float(row["end"])
            if end <= start:
                errors.append(f"{unit.unit_id}: non-positive subtitle duration")
            if start < previous_end - 0.011:
                errors.append(f"{unit.unit_id}: overlapping subtitle page")
            previous_end = max(previous_end, end)
            visible = sum(character not in PUNCTUATION + r"\N" for character in text)
            cps = visible / max(0.001, end - start)
            if cps > max_cps:
                reading_speed.append({"unit_id": unit.unit_id, "cps": round(cps, 6)})
    missing = sorted({unit.unit_id for unit in plan.units} - set(by_unit))
    if missing:
        errors.append(f"missing subtitle units: {len(missing)}")
    if reading_speed:
        errors.append(f"subtitle pages above {max_cps:g} chars/s: {len(reading_speed)}")
    layout = ass_layout(ass)
    if layout["status"] != STATUS_PASSED:
        errors.extend(layout["errors"])
    return {
        "status": STATUS_FAILED if errors else STATUS_PASSED,
        "exact_locked_text": not any("text differs" in error for error in errors),
        "missing_units": missing,
        "reading_speed_failures": reading_speed,
        "layout": layout,
        "errors": errors,
    }


def evaluate_episode_admission(
    *,
    settings: Settings,
    plan: ProductionPlan,
    media_qc: dict,
    ass: Path,
    clean_video: Path,
    delivered_video: Path,
    subtitle_events: list[dict],
    asr_report: dict,
    face_consistency_report: dict | None = None,
) -> dict:
    subtitle = evaluate_subtitle_structure(plan, subtitle_events, ass, settings.max_subtitle_cps)
    burn_in = evaluate_subtitle_burn_in(clean_video, delivered_video, subtitle_events)
    plan_dict = plan.model_dump(mode="json")
    speech = evaluate_asr(
        plan_dict,
        asr_report,
        aggregate_cer_max=settings.max_asr_cer,
        turn_cer_max=settings.max_turn_cer,
    )
    checks = {
        "media_qc": {"status": STATUS_PASSED if media_qc.get("passed") else STATUS_FAILED, "report": media_qc},
        "subtitle_structure": subtitle,
        "subtitle_burn_in": burn_in,
        "speech_content": speech,
    }
    evidence_passed = all(check["status"] == STATUS_PASSED for check in checks.values())
    submission_eligible = settings.admission_mode == "production" and settings.provider != "mock"
    return {
        "schema_version": 2,
        "policy_revision": ADMISSION_POLICY_REVISION,
        "passed": evidence_passed and (submission_eligible or settings.admission_mode == "preview"),
        "evidence_passed": evidence_passed,
        "submission_eligible": submission_eligible and evidence_passed,
        "admission_mode": settings.admission_mode,
        "provider": settings.provider,
        "video_id": plan.video_id,
        "source_text_sha256": plan.source_text_sha256,
        "style_fingerprint": plan.style_fingerprint,
        "backend_identity": admission_backend_identity(settings),
        "thresholds": {
            "aggregate_asr_cer_max": settings.max_asr_cer,
            "turn_asr_cer_max": settings.max_turn_cer,
            "subtitle_cps_max": settings.max_subtitle_cps,
        },
        "checks": checks,
        "informational_metrics": {
            "face_consistency": face_consistency_report
            or {"status": "unavailable", "detail": "no visible dialogue keyframes"}
        },
    }
