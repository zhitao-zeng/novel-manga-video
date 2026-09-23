"""Publish card copies under the layout the provider's public URL expects.

The video provider's asset library downloads reference images from a public URL
(<base>/<novel>/<asset>/<view>-<sha12><ext>); that URL is answered by whatever static server the
tunnel points at.  publish_cards.sh used to place those copies by hand and was never in this
repo; this module is the in-repo home of that step.  Copies are content-keyed (a redrawn card
earns a new name, an unchanged one never a second) and land by atomic rename.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

IMAGE_SUFFIXES = {".jpeg", ".jpg", ".png", ".webp"}


def publish_root(default_output_root: Path | None = None) -> Path:
    """Where published copies live: NOVEL_ASSET_PUBLISH_DIR, else <output_root>/.published."""
    configured = (os.getenv("NOVEL_ASSET_PUBLISH_DIR") or "").strip()
    if configured:
        return Path(configured)
    return Path(default_output_root or "outputs") / ".published"


def published_relpath(path: Path, digest: str) -> Path:
    """<novel>/<asset>/<view>-<sha12><ext> - the exact layout public_card_url() builds."""
    novel = path.parents[3].name if len(path.parents) > 3 else "novel"
    return Path(novel) / path.parent.name / f"{path.stem}-{digest[:12]}{path.suffix}"


def publish_file(path: Path, root: Path) -> Path:
    """Copy one card into the publish root, content-keyed; returns the published path."""
    path = Path(path)
    if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
        raise FileNotFoundError(f"not a publishable image: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    target = Path(root) / published_relpath(path, digest)
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as staging:
        staging.write(path.read_bytes())
        staging_path = Path(staging.name)
    os.replace(staging_path, target)
    return target


def publish_paths(paths, root: Path) -> list[Path]:
    return [publish_file(p, root) for p in paths]
