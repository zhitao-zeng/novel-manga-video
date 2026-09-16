"""Asset manifests and previously accepted reference records; original locking and formats."""
from __future__ import annotations

from pathlib import Path
import json
import fcntl
import threading
from ..util import atomic_write_json
from ..production_models import AssetRecord, SeriesAssetManifest

REPAIR_LOCK = threading.Lock()


MANIFEST_LOCK = threading.Lock()


PRIVACY_OK_FILE = "series_assets/.privacy_ok.json"


def load_privacy_ok(novel_dir: Path) -> set[str]:
    try:
        return set(json.loads((novel_dir / PRIVACY_OK_FILE).read_text(encoding="utf-8")).get("paths", []))
    except (OSError, ValueError):
        return set()


def record_privacy_ok(novel_dir: Path, paths) -> None:
    """Remember cards that Seedance accepted, so a later run (or a parallel one)
    never redraws a proven card just because it was the first thing rejected.
    The read-modify-write is guarded by a file lock: episodes render in
    parallel processes and finish clips at the same moment."""
    target = novel_dir / PRIVACY_OK_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    with REPAIR_LOCK, open(target.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        merged = load_privacy_ok(novel_dir) | {str(p) for p in paths}
        atomic_write_json(target, {"paths": sorted(merged)})


def merge_manifest(root, style_fingerprint, characters, locations, voices):
    manifest_path = root / "manifest.json"
    with MANIFEST_LOCK, open(root / ".manifest.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        characters = {**{row["asset_id"]: row for row in existing.get("characters", [])}, **characters}
        locations = {**{row["asset_id"]: row for row in existing.get("locations", [])}, **locations}
        voices = {**(existing.get("voice_assignments") or {"narrator": "native:narrator"}), **voices}
        manifest = SeriesAssetManifest(
            style_fingerprint=style_fingerprint,
            characters=[AssetRecord(**characters[key]) for key in sorted(characters)],
            locations=[AssetRecord(**locations[key]) for key in sorted(locations)],
            voice_assignments=voices,
        )
        atomic_write_json(manifest_path, manifest.model_dump(mode="json"))
    return manifest
