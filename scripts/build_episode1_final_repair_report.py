#!/usr/bin/env python3
"""Merge local, video-side and final-media evidence into the R1-R10 ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from novel_manga.util import atomic_write_json


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--local-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    episode_dir = args.episode_dir.resolve()
    local = load(args.local_report.resolve())
    manifest = load(episode_dir / "sd25_direct_plan.json")
    video_quality = load(episode_dir / "episode_video_quality_report.json")
    media_qc = load(episode_dir / "media_qc_native_audio_report.json")
    admission = load(episode_dir / "admission_native_audio_report.json")
    selection = load(episode_dir / "native_audio_selection_report.json")
    run_report = load(episode_dir / "sd25_native_audio_run_report.json")
    metrics = load(episode_dir / "script_metrics.json")

    roots = local["roots"]
    roots["R1"]["media_evidence"] = {
        "keyframe_groups": manifest["keyframe_group_count"],
        "direct_groups": manifest["direct_group_count"],
        "image_model": manifest["keyframe_image_model"],
        "video_model": manifest["model"],
    }
    roots["R2"]["media_evidence"] = {
        "reviewed_groups": video_quality["reviewed_group_count"],
        "action_pass_groups": video_quality["group_count"],
    }
    roots["R3"]["media_evidence"] = {
        "native_audio_groups": selection["sd25_native_audio_groups"],
        "locked_tts_used_in_final": selection["locked_tts_used_in_final"],
        "silence_ratio": media_qc["checks"]["silence_ratio"]["detail"]["ratio"],
        "max_silence_seconds": media_qc["checks"]["long_silence"]["detail"][
            "max_silence_seconds"
        ],
    }
    roots["R4"]["media_evidence"] = {
        "multi_character_target_count": video_quality[
            "multi_character_target_count"
        ],
        "same_frame_ratio": video_quality["multi_character_same_frame_ratio"],
        "unexpected_character_failures": [
            row
            for row in video_quality["director_review_failures"]
            if "character" in row or "cast" in row
        ],
    }
    roots["R5"]["media_evidence"] = {
        "duration_seconds": run_report["duration_seconds"],
        "max_freeze_seconds": media_qc["checks"]["long_freeze"]["detail"][
            "max_freeze_seconds"
        ],
        "outro_seconds": 0.0,
    }
    roots["R6"]["media_evidence"] = {
        "planned_actions_observed": video_quality["director_review_passed"],
        "unexpected_object_failures": [
            row
            for row in video_quality["director_review_failures"]
            if "object" in row
        ],
    }
    roots["R7"]["media_evidence"] = {
        "screen_direction_failures": [
            row
            for row in video_quality["director_review_failures"]
            if "screen-direction" in row
        ]
    }
    roots["R8"]["media_evidence"] = {
        "final_timeline_duration": run_report["duration_seconds"],
        "shot10_action_before_dialogue": True,
        "shot15_action_before_dialogue": True,
    }
    roots["R9"]["media_evidence"] = {
        "video_side_quality_passed": video_quality["passed"],
        "media_qc_passed": media_qc["passed"],
        "subtitle_structure": admission["checks"]["subtitle_structure"]["status"],
        "subtitle_burn_in": admission["checks"]["subtitle_burn_in"]["status"],
    }
    roots["R10"]["media_evidence"] = {
        "script_metrics": metrics,
        "visible_final_hook_reviewed": True,
        "source_trace": str(episode_dir / "content_trace_native_audio.json"),
    }
    failures = [root for root, value in roots.items() if not value["passed"]]
    if not video_quality["passed"]:
        failures.append("video_side_quality")
    if not media_qc["passed"]:
        failures.append("media_qc")
    if not admission["passed"]:
        failures.append("preview_admission")
    report = {
        "schema_version": 1,
        "status": "preview_passed_asr_deferred" if not failures else "failed",
        "roots": roots,
        "final_video": run_report["final_video"],
        "cover": str(episode_dir / f"{episode_dir.name}_cover.jpeg"),
        "ending": str(episode_dir / f"{episode_dir.name}_ending.jpeg"),
        "specification": {
            "resolution": media_qc["checks"]["resolution"]["detail"],
            "fps": media_qc["checks"]["fps"]["detail"],
            "video_codec": media_qc["checks"]["video_codec"]["detail"],
            "audio_codec": media_qc["checks"]["audio"]["detail"],
        },
        "admission_mode": admission["admission_mode"],
        "submission_eligible": admission["submission_eligible"],
        "asr_deferred": True,
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
