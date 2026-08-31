#!/usr/bin/env python3
"""Extract reusable single-view assets from approved/candidate 3D review cards.

The generated cards intentionally remain human-review artifacts.  Production
conditioning consumes the panel crops written by this script, never the whole
multi-view board.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from PIL import Image


ROOT = Path(
    "outputs/ftj-anime-api10-v1/ftj-anime-api10/series_assets"
)
STYLE_MASTER = (
    "characters/character_001/versions/3d_character_card_v2_stylized/"
    "review_character_card.png"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def crop_ratio(image: Image.Image, box: tuple[float, float, float, float]) -> Image.Image:
    width, height = image.size
    left, top, right, bottom = box
    return image.crop(
        (
            round(left * width),
            round(top * height),
            round(right * width),
            round(bottom * height),
        )
    )


def save_crops(
    card: Path,
    output_dir: Path,
    crops: dict[str, tuple[float, float, float, float]],
) -> dict[str, str]:
    with Image.open(card).convert("RGB") as image:
        saved = {}
        for filename, box in crops.items():
            output = output_dir / filename
            crop_ratio(image, box).save(output, "PNG", optimize=True)
            saved[filename.removesuffix(".png")] = str(output.relative_to(ROOT))
    return saved


def character_card_dir(character_dir: Path) -> Path:
    if character_dir.name == "character_001":
        return character_dir / "versions" / "3d_character_card_v2_stylized"
    return character_dir / "versions" / "3d_character_card_v1_stylized"


def main() -> int:
    production_manifest = json.loads(
        (ROOT / "manifest.json").read_text(encoding="utf-8")
    )
    location_source_by_id = {
        row["asset_id"]: row["primary_image"]
        for row in production_manifest.get("locations", [])
    }
    character_crops = {
        "portrait.png": (0.00, 0.00, 0.43, 1.00),
        "front_fullbody.png": (0.41, 0.08, 0.63, 0.90),
        "profile_fullbody.png": (0.62, 0.08, 0.80, 0.90),
        "back_fullbody.png": (0.79, 0.08, 0.98, 0.90),
    }
    location_crops = {
        "establishing_view.png": (0.01, 0.06, 0.55, 0.82),
        "dialogue_angle_a.png": (0.56, 0.06, 0.78, 0.45),
        "dialogue_reverse_b.png": (0.78, 0.06, 0.99, 0.45),
        "performance_zone.png": (0.56, 0.46, 0.78, 0.82),
        "detail_view.png": (0.78, 0.46, 0.99, 0.82),
    }
    characters = []
    for character_dir in sorted((ROOT / "characters").glob("character_*")):
        spec = json.loads((character_dir / "spec.json").read_text(encoding="utf-8"))
        card_dir = character_card_dir(character_dir)
        card = card_dir / "review_character_card.png"
        if not card.is_file():
            raise FileNotFoundError(card)
        views = save_crops(card, card_dir, character_crops)
        status = (
            "user_approved"
            if character_dir.name == "character_001"
            else "generated_pending_user_review"
        )
        existing_record = {}
        if (card_dir / "card.json").is_file():
            existing_record = json.loads(
                (card_dir / "card.json").read_text(encoding="utf-8")
            )
        record = {
            **existing_record,
            "schema_version": 1,
            "asset_id": character_dir.name,
            "character_name": spec["name"],
            "version": card_dir.name,
            "status": status,
            "style_master": STYLE_MASTER,
            "identity_source": str(
                (character_dir / "turnaround.jpeg").relative_to(ROOT)
            ),
            "review_card": str(card.relative_to(ROOT)),
            "review_card_sha256": sha256(card),
            "views": views,
            "view_policy": (
                "Use exactly one matching character view per shot plus the approved "
                "location view; never condition a shot on the whole review card."
            ),
            "generated_with": "built-in imagegen identity/style reference workflow",
            "recorded_date": date.today().isoformat(),
        }
        (card_dir / "card.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        characters.append(record)

    locations = []
    for location_dir in sorted((ROOT / "locations").glob("location_*")):
        spec = json.loads((location_dir / "spec.json").read_text(encoding="utf-8"))
        card_dir = location_dir / "versions" / "3d_location_card_v1_stylized"
        card = card_dir / "review_location_card.png"
        if not card.is_file():
            raise FileNotFoundError(card)
        views = save_crops(card, card_dir, location_crops)
        existing_record = {}
        if (card_dir / "card.json").is_file():
            existing_record = json.loads(
                (card_dir / "card.json").read_text(encoding="utf-8")
            )
        record = {
            **existing_record,
            "schema_version": 1,
            "asset_id": location_dir.name,
            "location_name": spec["name"],
            "version": card_dir.name,
            "status": "generated_pending_user_review",
            "style_master": (
                "locations/location_001/versions/3d_location_card_v1_stylized/"
                "review_location_card.png"
            ),
            "structure_source": location_source_by_id.get(
                location_dir.name,
                str((location_dir / "establishing.jpeg").relative_to(ROOT)),
            ),
            "review_card": str(card.relative_to(ROOT)),
            "review_card_sha256": sha256(card),
            "views": views,
            "view_policy": (
                "Use the establishing view for spatial setup, angle A/reverse B for "
                "dialogue, the performance-zone view for blocking, and the detail view "
                "only for the registered prop or material. Never pass the whole card."
            ),
            "empty_scene_required": True,
            "generated_with": "built-in imagegen structure/style reference workflow",
            "recorded_date": date.today().isoformat(),
        }
        (card_dir / "card.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        locations.append(record)

    manifest = {
        "schema_version": 1,
        "status": "candidate_asset_library_pending_user_review",
        "style": "premium stylized Chinese 3D animation",
        "style_master": STYLE_MASTER,
        "character_count": len(characters),
        "location_count": len(locations),
        "characters": characters,
        "locations": locations,
        "production_manifest_switched": False,
        "approval_policy": (
            "Approve the candidate library before changing series_assets/manifest.json."
        ),
    }
    (ROOT / "3d_asset_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "characters": len(characters),
                "locations": len(locations),
                "manifest": str(ROOT / "3d_asset_manifest.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
