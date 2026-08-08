#!/usr/bin/env python3
"""Create a unit-addressed, pitch-preserving speed-adjusted Qwen TTS cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from novel_manga.multivoice import load_multivoice_script


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--raw-audio-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--speed", type=float)
    parser.add_argument(
        "--role-speed",
        action="append",
        default=[],
        metavar="ROLE=SPEED",
        help="role-specific atempo value; may be repeated and overrides --speed",
    )
    parser.add_argument(
        "--role-pad",
        action="append",
        default=[],
        metavar="ROLE=HEAD,TAIL",
        help="role-specific leading and trailing silence in seconds; may be repeated",
    )
    return parser.parse_args()


def parse_role_speeds(values: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for value in values:
        role, separator, raw_speed = value.rpartition("=")
        if not separator or not role.strip():
            raise ValueError(f"invalid --role-speed value: {value!r}")
        speed = float(raw_speed)
        if not 0.5 <= speed <= 2.0:
            raise ValueError(f"speed for role {role!r} must be in [0.5, 2.0]")
        result[role] = speed
    return result


def parse_role_pads(values: list[str]) -> dict[str, tuple[float, float]]:
    result: dict[str, tuple[float, float]] = {}
    for value in values:
        role, separator, raw_padding = value.rpartition("=")
        parts = [item.strip() for item in raw_padding.split(",")]
        if not separator or not role.strip() or len(parts) != 2:
            raise ValueError(f"invalid --role-pad value: {value!r}")
        head, tail = (float(item) for item in parts)
        if not 0.0 <= head <= 2.0 or not 0.0 <= tail <= 2.0:
            raise ValueError(f"padding for role {role!r} must be in [0, 2] seconds")
        result[role] = (head, tail)
    return result


def duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: object) -> None:
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)


def main() -> int:
    args = parse_args()
    if args.speed is None and not args.role_speed:
        raise ValueError("provide --speed, --role-speed, or both")
    if args.speed is not None and not 0.5 <= args.speed <= 2.0:
        raise ValueError("speed must be in [0.5, 2.0] for one ffmpeg atempo stage")
    role_speeds = parse_role_speeds(args.role_speed)
    role_pads = parse_role_pads(args.role_pad)
    script = load_multivoice_script(args.script)
    unresolved_roles = sorted(
        {item.role for shot in script.shots for item in shot.turns}
        - role_speeds.keys()
    )
    if args.speed is None and unresolved_roles:
        raise ValueError(
            "roles without a speed and no --speed fallback: " + ", ".join(unresolved_roles)
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for shot in script.shots:
        for turn_index, item in enumerate(shot.turns, 1):
            speed = role_speeds.get(item.role, args.speed)
            assert speed is not None
            head_pad, tail_pad = role_pads.get(item.role, (0.0, 0.0))
            unit_id = f"shot_{shot.index:03d}_turn_{turn_index:02d}"
            source = args.raw_audio_dir / "turn_audio" / f"{unit_id}.wav"
            output = args.output_dir / f"{unit_id}.wav"
            if not source.is_file() or source.stat().st_size < 1000:
                raise FileNotFoundError(source)
            filters = [f"atempo={speed:.8f}"]
            if head_pad:
                filters.append(f"adelay={round(head_pad * 1000)}")
            if tail_pad:
                filters.append(f"apad=pad_dur={tail_pad:.8f}")
            audio_filter = ",".join(filters)
            request = {
                "unit_id": unit_id,
                "role": item.role,
                "text": item.text,
                "source": str(source.resolve()),
                "source_sha256": sha256(source),
                "speed": speed,
                "head_pad_seconds": head_pad,
                "tail_pad_seconds": tail_pad,
                "filter": audio_filter,
            }
            request_path = output.with_suffix(".wav.request.json")
            cache_hit = False
            if output.is_file() and request_path.is_file():
                cache_hit = json.loads(request_path.read_text(encoding="utf-8")) == request
            if not cache_hit:
                partial = output.with_suffix(".partial.wav")
                subprocess.run(
                    [
                        "ffmpeg", "-y", "-v", "error", "-i", str(source),
                        "-af", audio_filter, "-ar", "24000", "-ac", "1",
                        "-c:a", "pcm_s16le", str(partial),
                    ],
                    check=True,
                )
                os.replace(partial, output)
                atomic_json(request_path, request)
            source_seconds = duration(source)
            output_seconds = duration(output)
            speech_seconds = output_seconds - head_pad - tail_pad
            measured_speed = source_seconds / max(0.001, speech_seconds)
            if abs(measured_speed - speed) > 0.025:
                raise RuntimeError(f"{unit_id} measured speed {measured_speed:.6f} != {speed:.6f}")
            rows.append({
                **request,
                "cache_hit": cache_hit,
                "source_seconds": round(source_seconds, 6),
                "output_seconds": round(output_seconds, 6),
                "measured_speed": round(measured_speed, 6),
                "output": str(output.resolve()),
                "output_sha256": sha256(output),
            })
    total_source = sum(row["source_seconds"] for row in rows)
    total_output = sum(row["output_seconds"] for row in rows)
    by_role: dict[str, dict] = {}
    for role in sorted({row["role"] for row in rows}):
        role_rows = [row for row in rows if row["role"] == role]
        role_source = sum(row["source_seconds"] for row in role_rows)
        role_output = sum(row["output_seconds"] for row in role_rows)
        role_padding = sum(
            row["head_pad_seconds"] + row["tail_pad_seconds"] for row in role_rows
        )
        by_role[role] = {
            "requested_speed": role_rows[0]["speed"],
            "measured_speed": round(role_source / max(0.001, role_output - role_padding), 6),
            "unit_count": len(role_rows),
            "source_seconds": round(role_source, 6),
            "output_seconds": round(role_output, 6),
            "head_pad_seconds": role_rows[0]["head_pad_seconds"],
            "tail_pad_seconds": role_rows[0]["tail_pad_seconds"],
        }
    report = {
        "requested_speed": args.speed,
        "requested_role_speeds": role_speeds,
        "requested_role_pads": {
            role: {"head": padding[0], "tail": padding[1]}
            for role, padding in role_pads.items()
        },
        "measured_speed": round(
            total_source / max(
                0.001,
                total_output
                - sum(row["head_pad_seconds"] + row["tail_pad_seconds"] for row in rows),
            ),
            6,
        ),
        "unit_count": len(rows),
        "source_seconds": round(total_source, 6),
        "output_seconds": round(total_output, 6),
        "roles": by_role,
        "units": rows,
    }
    atomic_json(args.output_dir / "speed_report.json", report)
    print(json.dumps({
        key: report[key]
        for key in (
            "unit_count", "requested_speed", "requested_role_speeds", "requested_role_pads",
            "measured_speed", "source_seconds", "output_seconds", "roles",
        )
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
