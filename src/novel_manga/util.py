from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, TypeVar


T = TypeVar("T")


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(partial, path)


def run(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=capture)


def media_duration(path: Path) -> float:
    result = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(result.stdout.strip())


def retry(operation: Callable[[], T], attempts: int = 3, base_delay: float = 1.0) -> T:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as error:  # adapters re-raise HTTP and validation errors
            last_error = error
            if attempt + 1 < attempts:
                time.sleep(base_delay * (2 ** attempt))
    assert last_error is not None
    raise last_error
