"""Existing take identity used by review caches and repair history."""
from __future__ import annotations

from pathlib import Path


def take_identity(video: Path) -> list[int] | None:
    """The file behind a path - inode, size, mtime in ns: a rename keeps them all, another take has other ones."""
    try:
        stat = video.stat()
    except OSError:
        return None
    return [stat.st_ino, stat.st_size, stat.st_mtime_ns]
