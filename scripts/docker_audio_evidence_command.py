#!/usr/bin/env python3
"""Bridge host temporary evidence outputs through a Docker-mounted path."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import uuid
from pathlib import Path


CONTAINER = "novel-ftj3-i2-it-h3-gpu1"
CLI = "/opt/venvs/controller/bin/python /app/runtime/local_model_cli.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("asr", "align"))
    parser.add_argument("--unit-id", required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    audio = args.audio.resolve()
    output = args.output.resolve()
    if not audio.is_file():
        raise FileNotFoundError(audio)
    bridge_dir = audio.parent / ".evidence_bridge"
    bridge_dir.mkdir(parents=True, exist_ok=True)
    bridge = bridge_dir / f"{args.unit_id}-{args.kind}-{uuid.uuid4().hex}.json"
    command = [
        "docker",
        "exec",
        CONTAINER,
        *CLI.split(),
        args.kind,
        "--unit-id",
        args.unit_id,
        "--audio",
        str(audio),
        "--text",
        args.text,
        "--output",
        str(bridge),
    ]
    subprocess.run(command, check=True)
    if not bridge.is_file() or bridge.stat().st_size == 0:
        raise RuntimeError(f"container evidence command did not create {bridge}")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bridge, output)
    bridge.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
