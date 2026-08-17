#!/usr/bin/env python3
"""Design one reusable Qwen voice and clone it across unit-addressed lines."""

from __future__ import annotations

import argparse
import gc
import importlib.machinery
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import soundfile as sf
import torch


def _stub_unused_torchaudio_25hz(model_dir: Path) -> None:
    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    if config.get("tokenizer_type") != "qwen3_tts_tokenizer_12hz":
        raise ValueError("the torchaudio shim only supports Qwen3-TTS 12 Hz")

    def stub(name: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None)
        return module

    torchaudio = stub("torchaudio")
    compliance = stub("torchaudio.compliance")
    kaldi = stub("torchaudio.compliance.kaldi")
    compliance.kaldi = kaldi
    torchaudio.compliance = compliance
    sys.modules["torchaudio"] = torchaudio
    sys.modules["torchaudio.compliance"] = compliance
    sys.modules["torchaudio.compliance.kaldi"] = kaldi


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice-design-model", type=Path, required=True)
    parser.add_argument("--clone-model", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--speed", type=float, default=1.12)
    parser.add_argument("--seed", type=int, default=20260810)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.5 <= args.speed <= 2.0:
        raise ValueError("speed must be in [0.5, 2.0]")
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    reference_text = str(profile["reference_text"])
    instruction = str(profile["instruction"])
    turns = list(profile["turns"])
    if not turns or any(not row.get("unit_id") or not row.get("text") for row in turns):
        raise ValueError("profile turns require unit_id and text")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    _stub_unused_torchaudio_25hz(args.voice_design_model)
    from qwen_tts import Qwen3TTSModel

    design_model = Qwen3TTSModel.from_pretrained(
        str(args.voice_design_model.resolve()),
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    reference_wavs, sample_rate = design_model.generate_voice_design(
        text=reference_text,
        language="Chinese",
        instruct=instruction,
    )
    reference = args.output_dir / "voice_reference.wav"
    sf.write(reference, np.asarray(reference_wavs[0], dtype=np.float32), sample_rate)
    reference_array = np.asarray(reference_wavs[0], dtype=np.float32)
    del design_model, reference_wavs
    gc.collect()
    torch.cuda.empty_cache()

    clone_model = Qwen3TTSModel.from_pretrained(
        str(args.clone_model.resolve()),
        device_map="cuda:0",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    clone_prompt = clone_model.create_voice_clone_prompt(
        ref_audio=(reference_array, sample_rate),
        ref_text=reference_text,
    )
    texts = [str(row["text"]) for row in turns]
    generated, generated_rate = clone_model.generate_voice_clone(
        text=texts,
        language=["Chinese"] * len(texts),
        voice_clone_prompt=clone_prompt,
    )
    rows = []
    for turn, waveform in zip(turns, generated, strict=True):
        unit_id = str(turn["unit_id"])
        raw = args.output_dir / f"{unit_id}.raw.wav"
        output = args.output_dir / f"{unit_id}.wav"
        sf.write(raw, np.asarray(waveform, dtype=np.float32), generated_rate)
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", str(raw),
                "-af", f"atempo={args.speed:.8f}", "-ar", "24000", "-ac", "1",
                "-c:a", "pcm_s16le", str(output),
            ],
            check=True,
        )
        raw.unlink()
        rows.append(
            {
                "unit_id": unit_id,
                "text": str(turn["text"]),
                "output": output.name,
                "speed": args.speed,
            }
        )
    report = {
        "voice_id": str(profile.get("voice_id", "designed-clone")),
        "reference_text": reference_text,
        "instruction": instruction,
        "reference_audio": reference.name,
        "seed": args.seed,
        "speed": args.speed,
        "turns": rows,
    }
    partial = args.output_dir / "generation_report.json.partial"
    partial.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(partial, args.output_dir / "generation_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
