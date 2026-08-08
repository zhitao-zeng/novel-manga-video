from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

from .production_models import ProductionPlan, SeriesAssetManifest


def _region_crops(path: Path, *, reference: bool) -> list[Image.Image]:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    width, height = image.size
    short = min(width, height)
    centers_x = (0.16, 0.33, 0.5, 0.67, 0.84) if reference else (0.25, 0.5, 0.75)
    centers_y = (0.22, 0.34) if reference else (0.28, 0.4)
    scales = (0.2, 0.28, 0.38) if reference else (0.26, 0.38, 0.52)
    crops: list[Image.Image] = []
    for center_x in centers_x:
        for center_y in centers_y:
            for scale in scales:
                size = max(24, round(short * scale))
                left = max(0, min(width - size, round(width * center_x - size / 2)))
                top = max(0, min(height - size, round(height * center_y - size / 2)))
                crop = image.crop((left, top, left + size, top + size))
                if ImageStat.Stat(crop.convert("L")).stddev[0] >= 6.0:
                    crops.append(crop)
    return crops


def _difference_hash(image: Image.Image) -> int:
    gray = image.convert("L").resize((17, 16), Image.Resampling.LANCZOS)
    pixels = gray.tobytes()
    result = 0
    for y in range(16):
        offset = y * 17
        for x in range(16):
            result = (result << 1) | int(pixels[offset + x] > pixels[offset + x + 1])
    return result


def _color_histogram(image: Image.Image) -> tuple[float, ...]:
    histogram = image.resize((64, 64), Image.Resampling.BILINEAR).histogram()
    bins: list[float] = []
    for channel in range(3):
        values = histogram[channel * 256 : (channel + 1) * 256]
        grouped = [sum(values[index : index + 16]) for index in range(0, 256, 16)]
        total = max(1, sum(grouped))
        bins.extend(value / total for value in grouped)
    return tuple(bins)


def _descriptor(image: Image.Image) -> tuple[int, int, tuple[float, ...]]:
    edge = image.convert("L").filter(ImageFilter.FIND_EDGES)
    return _difference_hash(image), _difference_hash(edge), _color_histogram(image)


def _descriptors(path: Path, *, reference: bool) -> list[tuple[int, int, tuple[float, ...]]]:
    return [_descriptor(region) for region in _region_crops(path, reference=reference)]


def _descriptor_similarity(
    left: tuple[int, int, tuple[float, ...]],
    right: tuple[int, int, tuple[float, ...]],
) -> float:
    appearance = 1.0 - ((left[0] ^ right[0]).bit_count() / 256)
    edges = 1.0 - ((left[1] ^ right[1]).bit_count() / 256)
    color = sum(min(a, b) for a, b in zip(left[2], right[2], strict=True)) / 3.0
    return max(0.0, min(1.0, 0.5 * appearance + 0.35 * color + 0.15 * edges))


def face_region_similarity(reference: Path, candidate: Path) -> dict:
    """Return a cheap manga portrait-region similarity proxy, not face recognition."""

    reference_descriptors = _descriptors(reference, reference=True)
    candidate_descriptors = _descriptors(candidate, reference=False)
    if not reference_descriptors or not candidate_descriptors:
        return {
            "status": "unavailable",
            "score": None,
            "detail": "no sufficiently detailed portrait regions",
        }
    best = max(
        _descriptor_similarity(left, right)
        for left in reference_descriptors
        for right in candidate_descriptors
    )
    return {
        "status": "scored",
        "score": round(best * 100.0, 2),
        "reference_region_count": len(reference_descriptors),
        "candidate_region_count": len(candidate_descriptors),
    }


def evaluate_face_consistency(
    *,
    novel_dir: Path,
    episode_dir: Path,
    plan: ProductionPlan,
    assets: SeriesAssetManifest,
) -> dict:
    character_map = {record.asset_id: record for record in assets.characters}
    reference_cache: dict[str, list[tuple[int, int, tuple[float, ...]]]] = {}
    rows: list[dict] = []
    for unit in plan.units:
        if not unit.speaking or not unit.character_asset_ids:
            continue
        record = character_map.get(unit.character_asset_ids[0])
        if record is None:
            continue
        reference = novel_dir / record.primary_image
        candidate = episode_dir / unit.keyframe_path
        if not reference.is_file() or not candidate.is_file():
            rows.append(
                {
                    "unit_id": unit.unit_id,
                    "character": unit.speaker_name,
                    "status": "unavailable",
                    "score": None,
                    "detail": "reference or keyframe is missing",
                }
            )
            continue
        if record.asset_id not in reference_cache:
            reference_cache[record.asset_id] = _descriptors(reference, reference=True)
        reference_descriptors = reference_cache[record.asset_id]
        candidate_descriptors = _descriptors(candidate, reference=False)
        if reference_descriptors and candidate_descriptors:
            best = max(
                _descriptor_similarity(left, right)
                for left in reference_descriptors
                for right in candidate_descriptors
            )
            score = {
                "status": "scored",
                "score": round(best * 100.0, 2),
                "reference_region_count": len(reference_descriptors),
                "candidate_region_count": len(candidate_descriptors),
            }
        else:
            score = {
                "status": "unavailable",
                "score": None,
                "detail": "no sufficiently detailed portrait regions",
            }
        rows.append(
            {
                "unit_id": unit.unit_id,
                "character": unit.speaker_name,
                "reference_asset": record.primary_image,
                "keyframe": unit.keyframe_path,
                **score,
            }
        )

    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get("score") is not None:
            grouped[str(row["character"])].append(float(row["score"]))
    characters = [
        {
            "character": character,
            "scored_units": len(scores),
            "average_score": round(sum(scores) / len(scores), 2),
            "minimum_score": round(min(scores), 2),
        }
        for character, scores in sorted(grouped.items())
    ]
    scores = [float(row["score"]) for row in rows if row.get("score") is not None]
    return {
        "schema_version": 1,
        "status": "informational" if scores else "unavailable",
        "blocking": False,
        "method": "lightweight-manga-portrait-region-perceptual-proxy-v1",
        "caveat": "This catches large identity drift but is not biometric face recognition.",
        "visible_dialogue_units": len(rows),
        "scored_units": len(scores),
        "average_score": round(sum(scores) / len(scores), 2) if scores else None,
        "minimum_score": round(min(scores), 2) if scores else None,
        "characters": characters,
        "units": rows,
    }
