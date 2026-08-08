from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Chinese WAV with a mounted sherpa-onnx VITS model")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--model-file", default="model.onnx")
    parser.add_argument("--sid", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--speed", type=float, default=1.12)
    parser.add_argument("text")
    args = parser.parse_args()

    import sherpa_onnx
    import soundfile as sf

    model_dir = args.model_dir.resolve()
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(model_dir / args.model_file),
                lexicon=str(model_dir / "lexicon.txt"),
                tokens=str(model_dir / "tokens.txt"),
            ),
            provider="cpu",
            num_threads=2,
        ),
        rule_fsts=",".join(str(model_dir / name) for name in ("phone.fst", "date.fst", "number.fst")),
        max_num_sentences=1,
    )
    if not config.validate():
        raise ValueError(f"invalid local TTS model configuration: {model_dir}")
    audio = sherpa_onnx.OfflineTts(config).generate(args.text, sid=args.sid, speed=args.speed)
    if not len(audio.samples):
        raise RuntimeError("local TTS returned empty audio")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.output, audio.samples, samplerate=audio.sample_rate, subtype="PCM_16")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
