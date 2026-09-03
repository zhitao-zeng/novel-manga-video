#!/usr/bin/env python3
"""Recognise a list of speech chunks with one SenseVoice model load (ASR venv)."""
from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
from pathlib import Path

import sherpa_onnx


def pcm(audio: Path, start: float, end: float) -> list[float]:
    process = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{max(0.05, end - start):.3f}", "-i", str(audio),
         "-ac", "1", "-ar", "16000", "-f", "s16le", "-acodec", "pcm_s16le", "-"],
        check=True, capture_output=True,
    )
    count = len(process.stdout) // 2
    return [sample / 32768.0 for sample in struct.unpack(f"<{count}h", process.stdout[: count * 2])]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--segments", type=Path, required=True, help="JSON list of [start, end] seconds")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model_dir = Path(os.environ["NOVEL_SENSEVOICE_MODEL_DIR"])
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model_dir / "model.int8.onnx"), tokens=str(model_dir / "tokens.txt"),
        language="zh", use_itn=False, num_threads=int(os.getenv("NOVEL_ASR_THREADS", "4")), provider="cpu",
    )
    rows = []
    for start, end in json.loads(args.segments.read_text(encoding="utf-8")):
        samples = pcm(args.audio, float(start), float(end)) + [0.0] * 8000
        stream = recognizer.create_stream()
        stream.accept_waveform(16000, samples)
        recognizer.decode_stream(stream)
        rows.append({"start": float(start), "end": float(end), "hypothesis": stream.result.text.strip()})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"backend": "sherpa-onnx-sensevoice-int8-2024-07-17", "segments": rows}, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
