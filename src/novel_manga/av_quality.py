from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path


STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_INCONCLUSIVE = "inconclusive"


def unit_id(item: dict) -> str:
    if item.get("unit_id"):
        return str(item["unit_id"])
    return f"shot_{int(item['shot_index']):03d}_turn_{int(item['turn_index']):02d}"


def normalize_spoken_text(text: str) -> str:
    return "".join(character for character in text if "\u4e00" <= character <= "\u9fff" or character.isalnum()).lower()


def evaluate_asr(
    plan: dict,
    report: dict | None,
    *,
    aggregate_cer_max: float = 0.12,
    turn_cer_max: float = 0.35,
) -> dict:
    planned = {unit_id(item): item for item in plan["units"]}
    if report is None:
        return {
            "status": STATUS_INCONCLUSIVE,
            "detail": "ASR report is missing",
            "missing_units": sorted(planned),
        }

    observed = {unit_id(item): item for item in report.get("turns", [])}
    missing = sorted(set(planned) - set(observed))
    unexpected = sorted(set(observed) - set(planned))
    text_mismatches: list[str] = []
    bad_turns: list[dict] = []
    for identifier, expected in planned.items():
        row = observed.get(identifier)
        if row is None:
            continue
        reference = normalize_spoken_text(str(row.get("reference", "")))
        expected_text = normalize_spoken_text(str(expected["text"]))
        if reference != expected_text:
            text_mismatches.append(identifier)
        cer = float(row.get("cer", math.inf))
        if not math.isfinite(cer) or cer > turn_cer_max:
            bad_turns.append(
                {
                    "unit_id": identifier,
                    "cer": cer,
                    "reference": row.get("reference"),
                    "hypothesis": row.get("hypothesis"),
                }
            )

    aggregate_cer = float(report.get("cer", math.inf))
    errors = []
    if missing:
        errors.append(f"missing ASR units: {len(missing)}")
    if unexpected:
        errors.append(f"unexpected ASR units: {len(unexpected)}")
    if text_mismatches:
        errors.append(f"ASR reference text mismatches: {len(text_mismatches)}")
    if not math.isfinite(aggregate_cer) or aggregate_cer > aggregate_cer_max:
        errors.append(f"aggregate CER {aggregate_cer:.6f} > {aggregate_cer_max:.6f}")
    if bad_turns:
        errors.append(f"per-turn CER failures: {len(bad_turns)}")
    return {
        "status": STATUS_FAILED if errors else STATUS_PASSED,
        "recognizer": report.get("recognizer"),
        "aggregate_cer": aggregate_cer,
        "aggregate_cer_max": aggregate_cer_max,
        "turn_cer_max": turn_cer_max,
        "missing_units": missing,
        "unexpected_units": unexpected,
        "reference_text_mismatches": text_mismatches,
        "bad_turns": bad_turns,
        "errors": errors,
    }


def evaluate_delivered_audio_energy(
    report: dict | None,
    *,
    minimum_peak_db: float = -35.0,
) -> dict:
    """Reject final-video turn extracts that contain effectively no audible speech."""
    if report is None:
        return {"status": STATUS_INCONCLUSIVE, "detail": "delivered audio report is missing"}
    turns = report.get("turns", [])
    missing = [str(row.get("unit_id", "unknown")) for row in turns if row.get("max_volume_db") is None]
    silent = [
        {
            "unit_id": str(row.get("unit_id", "unknown")),
            "max_volume_db": float(row["max_volume_db"]),
            "mean_volume_db": row.get("mean_volume_db"),
        }
        for row in turns
        if row.get("max_volume_db") is not None
        and float(row["max_volume_db"]) < minimum_peak_db
    ]
    if not turns or missing:
        status = STATUS_INCONCLUSIVE
    else:
        status = STATUS_FAILED if silent else STATUS_PASSED
    return {
        "status": status,
        "minimum_peak_db": minimum_peak_db,
        "turn_count": len(turns),
        "missing_measurements": missing,
        "silent_turns": silent,
    }


def _gray_frame(path: Path, seconds: float, *, width: int = 270, height: int = 480) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{max(0.0, seconds):.6f}",
            "-i",
            str(path),
            "-vf",
            f"scale={width}:{height},format=gray",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-",
        ],
        check=True,
        capture_output=True,
    )
    expected = width * height
    if len(result.stdout) != expected:
        raise RuntimeError(f"expected {expected} gray bytes from {path}, got {len(result.stdout)}")
    return result.stdout


def _band_difference(
    left: bytes,
    right: bytes,
    *,
    width: int,
    y_start: int,
    y_end: int,
    changed_threshold: int = 12,
) -> dict:
    count = max(1, width * (y_end - y_start))
    total = 0
    changed = 0
    peak = 0
    for y in range(y_start, y_end):
        offset = y * width
        for x in range(width):
            difference = abs(left[offset + x] - right[offset + x])
            total += difference
            changed += difference >= changed_threshold
            peak = max(peak, difference)
    return {
        "mean_abs_difference": round(total / count, 6),
        "changed_ratio": round(changed / count, 6),
        "peak_difference": peak,
    }


def evaluate_subtitle_burn_in(
    clean_video: Path,
    delivered_video: Path,
    events: list[dict],
    *,
    sample_count: int = 8,
) -> dict:
    if not clean_video.is_file() or not delivered_video.is_file():
        return {
            "status": STATUS_INCONCLUSIVE,
            "detail": "clean or delivered video is missing",
        }
    if not events:
        return {"status": STATUS_FAILED, "detail": "no subtitle events to sample"}
    count = min(max(1, sample_count), len(events))
    indices = sorted({round(index * (len(events) - 1) / max(1, count - 1)) for index in range(count)})
    samples = []
    for index in indices:
        event = events[index]
        seconds = (float(event["start"]) + float(event["end"])) / 2
        try:
            clean = _gray_frame(clean_video, seconds)
            delivered = _gray_frame(delivered_video, seconds)
        except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
            samples.append({"event_index": index, "seconds": seconds, "status": STATUS_INCONCLUSIVE, "error": str(error)})
            continue
        subtitle = _band_difference(clean, delivered, width=270, y_start=295, y_end=435)
        control = _band_difference(clean, delivered, width=270, y_start=30, y_end=135)
        signal = subtitle["changed_ratio"] - control["changed_ratio"]
        if control["changed_ratio"] > 0.1:
            status = STATUS_INCONCLUSIVE
        elif subtitle["changed_ratio"] >= 0.01 and signal >= 0.005:
            status = STATUS_PASSED
        else:
            status = STATUS_FAILED
        samples.append(
            {
                "event_index": index,
                "seconds": round(seconds, 6),
                "status": status,
                "subtitle_band": subtitle,
                "control_band": control,
                "signal_ratio": round(signal, 6),
            }
        )
    conclusive = [item for item in samples if item["status"] != STATUS_INCONCLUSIVE]
    passed = [item for item in conclusive if item["status"] == STATUS_PASSED]
    if len(conclusive) < min(3, count):
        status = STATUS_INCONCLUSIVE
    elif len(passed) / len(conclusive) >= 0.8:
        status = STATUS_PASSED
    else:
        status = STATUS_FAILED
    return {
        "status": status,
        "sample_count": len(samples),
        "conclusive_count": len(conclusive),
        "passed_count": len(passed),
        "samples": samples,
    }


def ass_layout(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    x_match = re.search(r"^PlayResX:\s*(\d+)", text, flags=re.MULTILINE)
    y_match = re.search(r"^PlayResY:\s*(\d+)", text, flags=re.MULTILINE)
    style_match = re.search(r"^Style:\s*Default,(.+)$", text, flags=re.MULTILINE)
    errors = []
    detail: dict[str, int | None] = {
        "play_res_x": int(x_match.group(1)) if x_match else None,
        "play_res_y": int(y_match.group(1)) if y_match else None,
        "font_size": None,
        "alignment": None,
        "margin_v": None,
    }
    if detail["play_res_x"] != 1080 or detail["play_res_y"] != 1920:
        errors.append("ASS PlayRes must be 1080x1920")
    if not style_match:
        errors.append("Default ASS style is missing")
    else:
        fields = style_match.group(1).split(",")
        if len(fields) < 22:
            errors.append("Default ASS style has too few fields")
        else:
            detail["font_size"] = int(float(fields[1]))
            detail["alignment"] = int(fields[17])
            detail["margin_v"] = int(fields[20])
            if not 42 <= detail["font_size"] <= 72:
                errors.append("ASS font size must be between 42 and 72")
            if detail["alignment"] != 2:
                errors.append("ASS subtitles must use bottom-center alignment 2")
            if detail["margin_v"] < 300:
                errors.append("ASS bottom safe margin must be at least 300 pixels")
    return {"status": STATUS_FAILED if errors else STATUS_PASSED, **detail, "errors": errors}
