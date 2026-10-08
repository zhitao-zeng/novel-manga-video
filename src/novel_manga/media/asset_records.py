"""Asset manifests and previously accepted reference records; original locking and formats."""
from __future__ import annotations

from pathlib import Path
import json
import fcntl
import threading
from ..util import atomic_write_json
from novel_manga.models.assets import AssetRecord, SeriesAssetManifest

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


def merge_manifest(root, style_fingerprint, characters, locations, voices, props=None):
    manifest_path = root / "manifest.json"
    with MANIFEST_LOCK, open(root / ".manifest.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        characters = {**{row["asset_id"]: row for row in existing.get("characters", [])}, **characters}
        locations = {**{row["asset_id"]: row for row in existing.get("locations", [])}, **locations}
        merged_props = {**{row["asset_id"]: row for row in existing.get("props", [])}, **(props or {})}
        voices = {**(existing.get("voice_assignments") or {"narrator": "native:narrator"}), **voices}
        manifest = SeriesAssetManifest(
            style_fingerprint=style_fingerprint,
            characters=[AssetRecord(**characters[key]) for key in sorted(characters)],
            locations=[AssetRecord(**locations[key]) for key in sorted(locations)],
            props=[AssetRecord(**merged_props[key]) for key in sorted(merged_props)],
            voice_assignments=voices,
        )
        payload = manifest.model_dump(mode="json")
        if not manifest.props:
            payload.pop("props", None)  # a prop-less book's manifest stays byte-identical
        atomic_write_json(manifest_path, payload)
    return manifest


def replace_card_verdict(root: Path, asset_id: str, old_request: str,
                         candidate: dict | None = None) -> None:
    """Archive the old verdict, then install an already accepted candidate's review."""
    path = root / "cards_review.json"
    if not path.is_file() and candidate is None:
        return
    from .asset_images import _archive_stale
    section = "characters" if asset_id.startswith("character_") else "locations"
    with (root / ".cards_review.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        report = (json.loads(path.read_text(encoding="utf-8")) if path.is_file()
                  else {"characters": {}, "locations": {}, "flags": [], "missing_cards": []})
        previous = (report.get(section) or {}).pop(asset_id, None)
        if previous is not None:
            directory = root / section / asset_id
            evidence = directory / "card_review.json"
            atomic_write_json(evidence, previous)
            _archive_stale(evidence, old_request)
        report["flags"] = [row for row in report.get("flags", [])
                           if not str(row).startswith(asset_id + " ")]
        report["missing_cards"] = [row for row in report.get("missing_cards", [])
                                   if not str(row).startswith(asset_id + " ")]
        missing_summary = "待重建的卡（所属章节建卡时生成）："
        report["flags"] = [row for row in report["flags"]
                           if not str(row).startswith(missing_summary)]
        if report["missing_cards"]:
            report["flags"].append(missing_summary + "、".join(report["missing_cards"]))
        if candidate is not None:
            entry = (candidate.get(section) or {}).get(asset_id)
            if entry is not None:
                report.setdefault(section, {})[asset_id] = entry
            if "policy" in candidate:
                report["policy"] = candidate["policy"]
        atomic_write_json(path, report)
