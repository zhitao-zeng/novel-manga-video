#!/usr/bin/env python3
"""Measure per-turn CER for a rendered multivoice script with SenseVoice."""

from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import time
from pathlib import Path

import sherpa_onnx


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=4)
    return parser.parse_args()


def normalize(text: str) -> str:
    text = re.sub(r"<\|.*?\|>", "", text).casefold()
    return "".join(char for char in text if char.isalnum() or "\u3400" <= char <= "\u9fff")


def edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def read_pcm16(path: Path, sample_rate: int = 16_000) -> list[float]:
    process = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sample_rate),
            "-f", "s16le", "-acodec", "pcm_s16le", "-",
        ],
        check=True,
        capture_output=True,
    )
    count = len(process.stdout) // 2
    samples = struct.unpack(f"<{count}h", process.stdout[: count * 2])
    return [sample / 32768.0 for sample in samples] + [0.0] * (sample_rate // 2)


def transcribe(recognizer: sherpa_onnx.OfflineRecognizer, path: Path) -> str:
    stream = recognizer.create_stream()
    stream.accept_waveform(16_000, read_pcm16(path))
    recognizer.decode_stream(stream)
    return stream.result.text.strip()


def main() -> None:
    args = parse_args()
    source = json.loads(args.script.read_text(encoding="utf-8"))
    model_path = args.model_dir / "model.int8.onnx"
    tokens_path = args.model_dir / "tokens.txt"
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model_path),
        tokens=str(tokens_path),
        language="zh",
        use_itn=False,
        num_threads=args.threads,
        provider="cpu",
    )

    started = time.perf_counter()
    total_errors = 0
    total_reference_chars = 0
    results = []
    for shot in source["shots"]:
        for turn_index, turn in enumerate(shot["turns"], start=1):
            audio_path = args.audio_dir / f"shot_{shot['index']:03d}_turn_{turn_index:02d}.wav"
            hypothesis = transcribe(recognizer, audio_path)
            reference_norm = normalize(turn["text"])
            hypothesis_norm = normalize(hypothesis)
            errors = edit_distance(reference_norm, hypothesis_norm)
            total_errors += errors
            total_reference_chars += len(reference_norm)
            results.append(
                {
                    "shot_index": shot["index"],
                    "turn_index": turn_index,
                    "role": turn["role"],
                    "audio": str(audio_path),
                    "reference": turn["text"],
                    "hypothesis": hypothesis,
                    "reference_normalized": reference_norm,
                    "hypothesis_normalized": hypothesis_norm,
                    "errors": errors,
                    "reference_chars": len(reference_norm),
                    "cer": round(errors / max(1, len(reference_norm)), 6),
                }
            )

    report = {
        "recognizer": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
        "turn_count": len(results),
        "total_errors": total_errors,
        "reference_chars": total_reference_chars,
        "cer": round(total_errors / max(1, total_reference_chars), 6),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "turns": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in report if key != "turns"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
