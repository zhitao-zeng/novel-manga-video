#!/usr/bin/env python3
"""Single-file SenseVoice adapter for NOVEL_ASR_COMMAND."""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import subprocess
from pathlib import Path

import sherpa_onnx


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit-id", required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model_dir = Path(os.environ["NOVEL_SENSEVOICE_MODEL_DIR"])
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(model_dir / "model.int8.onnx"),
        tokens=str(model_dir / "tokens.txt"),
        language="zh",
        use_itn=False,
        num_threads=int(os.getenv("NOVEL_ASR_THREADS", "4")),
        provider="cpu",
    )
    process = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(args.audio), "-ac", "1", "-ar", "16000", "-f", "s16le", "-acodec", "pcm_s16le", "-"],
        check=True,
        capture_output=True,
    )
    count = len(process.stdout) // 2
    pcm = struct.unpack(f"<{count}h", process.stdout[: count * 2])
    samples = [sample / 32768.0 for sample in pcm] + [0.0] * 8000
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, samples)
    recognizer.decode_stream(stream)
    raw_hypothesis = stream.result.text.strip()
    hypothesis = raw_hypothesis
    substitutions = []
    lexicon_raw = os.getenv("NOVEL_ASR_PROTECTED_LEXICON_JSON", "{}")
    lexicon = json.loads(lexicon_raw)
    if not isinstance(lexicon, dict):
        raise ValueError("NOVEL_ASR_PROTECTED_LEXICON_JSON must be an object")
    for canonical, aliases in lexicon.items():
        if canonical not in args.text:
            continue
        if not isinstance(aliases, list):
            raise ValueError("protected lexicon values must be string arrays")
        for alias in sorted((str(item) for item in aliases), key=len, reverse=True):
            if alias and alias in hypothesis:
                hypothesis = re.sub(re.escape(alias), canonical, hypothesis, count=1)
                substitutions.append({"from": alias, "to": canonical})
                break
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"unit_id": args.unit_id, "hypothesis": hypothesis, "raw_hypothesis": raw_hypothesis, "protected_lexicon_substitutions": substitutions, "backend": "sherpa-onnx-sensevoice-int8-2024-07-17+protected-lexicon-v1"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
