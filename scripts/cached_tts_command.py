#!/usr/bin/env python3
"""TTS command adapter that copies a pre-generated, unit-addressed Qwen WAV."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--voice", required=True)
    parser.add_argument("--instructions")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cache = Path(os.environ["NOVEL_QWEN_TTS_CACHE_DIR"])
    if args.output.name.startswith("attempt_") and args.output.parent.parent.name.startswith("shot_"):
        unit_id = args.output.parent.parent.name
    else:
        unit_id = args.output.stem
    source = cache / f"{unit_id}.wav"
    if not source.is_file() or source.stat().st_size < 1000:
        raise FileNotFoundError(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
