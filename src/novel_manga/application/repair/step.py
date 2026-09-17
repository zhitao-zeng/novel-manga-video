#!/usr/bin/env python3
"""Persist a worker's exit result so a restarted manager resumes its real step."""
import argparse
from pathlib import Path
import subprocess
import time

from novel_manga.util import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("worker command is required")
    try:
        code = subprocess.run(command).returncode
    except OSError:
        code = 127
    atomic_write_json(args.result, {"returncode": code, "finished_at": time.strftime("%F %T")})
    return code if code >= 0 else 128 - code
