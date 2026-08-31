from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from .production_models import ProductionPlan
from .util import atomic_write_json, media_duration


def _stream_contract(path: Path) -> dict:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return {"passed": False, "error": probe.stderr[-400:]}
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
        "passed": video is not None and audio is not None,
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
    }


def evaluate_generated_video_quality(
    *,
    episode_dir: Path,
    manifest: dict,
    plan: ProductionPlan,
    director_review: dict | None,
    report_path: Path,
    require_director_review: bool = True,
) -> dict:
    """Gate generated groups before final assembly.

    Semantic action/cast evidence is supplied by a director or VLM report.
    Face-similarity scores stay informational; explicit cast swaps, missing
    people and unexpected objects are blocking.
    """

    groups_by_id = {group.group_id: group for group in plan.visual_groups}
    automated_failures: list[str] = []
    group_rows: list[dict] = []
    for item in manifest.get("groups", []):
        group_id = str(item["group_id"])
        group = groups_by_id.get(group_id)
        if group is None:
            automated_failures.append(f"{group_id}:missing-production-contract")
            continue
        raw = episode_dir / group.raw_video_path
        if not raw.is_file():
            automated_failures.append(f"{group_id}:missing-raw-video")
            continue
        streams = _stream_contract(raw)
        actual = media_duration(raw)
        requested = float(item["generation_duration"])
        minimum = max(3.7, requested - 0.35)
        maximum = math.ceil(requested) + 0.75
        duration_ok = minimum <= actual <= maximum
        if not streams.get("passed"):
            automated_failures.append(f"{group_id}:missing-video-or-native-audio")
        if not duration_ok:
            automated_failures.append(f"{group_id}:generation-duration")
        request_meta = raw.with_suffix(raw.suffix + ".request.json")
        reference_audio_used = None
        if request_meta.is_file():
            reference_audio_used = bool(
                json.loads(request_meta.read_text(encoding="utf-8")).get(
                    "reference_audio_used"
                )
            )
            if reference_audio_used:
                automated_failures.append(f"{group_id}:reference-audio-used")
        group_rows.append(
            {
                "group_id": group_id,
                "raw_video": str(raw.relative_to(episode_dir)),
                "stream_contract": streams,
                "actual_duration": round(actual, 6),
                "requested_generation_duration": requested,
                "duration_ok": duration_ok,
                "reference_audio_used": reference_audio_used,
            }
        )

    review_failures: list[str] = []
    review_rows = {
        str(row["group_id"]): row
        for row in (director_review or {}).get("groups", [])
        if isinstance(row, dict) and row.get("group_id")
    }
    reviewed_count = 0
    multi_target_count = 0
    same_frame_pass_count = 0
    identity_scores: list[float] = []
    for item in manifest.get("groups", []):
        group_id = str(item["group_id"])
        expected_ids = {
            str(reference["asset_id"])
            for reference in item.get("references", [])
            if reference.get("role") == "character_identity_costume"
        }
        if len(expected_ids) >= 2:
            multi_target_count += 1
        review = review_rows.get(group_id)
        if review is None:
            if require_director_review:
                review_failures.append(f"{group_id}:director-review-missing")
            continue
        reviewed_count += 1
        observed_ids = {str(value) for value in review.get("visible_asset_ids", [])}
        cast_ok = observed_ids == expected_ids
        if len(expected_ids) >= 2 and cast_ok:
            same_frame_pass_count += 1
        if not bool(review.get("action_observed")):
            review_failures.append(f"{group_id}:planned-action-not-observed")
        if not cast_ok:
            review_failures.append(f"{group_id}:visible-cast-mismatch")
        if review.get("unexpected_named_characters"):
            review_failures.append(f"{group_id}:unexpected-character")
        if review.get("unexpected_objects"):
            review_failures.append(f"{group_id}:unexpected-object")
        if not bool(review.get("screen_direction_ok", True)):
            review_failures.append(f"{group_id}:screen-direction")
        score = review.get("identity_consistency_score")
        if isinstance(score, (int, float)):
            identity_scores.append(float(score))

    same_frame_ratio = (
        same_frame_pass_count / multi_target_count if multi_target_count else 1.0
    )
    if multi_target_count and same_frame_ratio < 1.0:
        review_failures.append("episode:multi-character-same-frame-ratio")

    failures = [*automated_failures, *review_failures]
    report = {
        "schema_version": 1,
        "policy": "episode-video-side-gates-v1",
        "passed": not failures,
        "automated_passed": not automated_failures,
        "director_review_passed": not review_failures,
        "group_count": len(manifest.get("groups", [])),
        "reviewed_group_count": reviewed_count,
        "multi_character_target_count": multi_target_count,
        "multi_character_same_frame_ratio": round(same_frame_ratio, 6),
        "identity_consistency_monitor": {
            "blocking": False,
            "mean_score": (
                round(sum(identity_scores) / len(identity_scores), 6)
                if identity_scores
                else None
            ),
            "sample_count": len(identity_scores),
        },
        "groups": group_rows,
        "automated_failures": automated_failures,
        "director_review_failures": review_failures,
        "failures": failures,
    }
    atomic_write_json(report_path, report)
    return report
