"""Asset readability checks and local contact sheets, without generation calls."""
from __future__ import annotations

from pathlib import Path
from PIL import Image
from .common import log

def asset_index(asset_id: str) -> int:
    return int(asset_id.rsplit("_", 1)[1])


def cards_sheet(novel_dir: Path, output: Path, height: int = 300, *, asset_ids: set[str] | None = None) -> Path | None:
    """Preview the selected cards; a chapter build must not stack the whole book
    into a JPEG taller than the format supports."""
    rows: list[list[Path]] = []
    for card_dir in sorted((novel_dir / "series_assets" / "characters").glob("character_*")):
        if asset_ids is not None and card_dir.name not in asset_ids:
            continue
        views = [card_dir / name for name in ("turnaround.jpeg", "expressions.jpeg") if (card_dir / name).is_file()]
        if views:
            rows.append(views)
    locations = sorted((novel_dir / "series_assets" / "locations").glob("location_*/establishing.jpeg"))
    if asset_ids is not None:
        locations = [path for path in locations if path.parent.name in asset_ids]
    for index in range(0, len(locations), 4):
        rows.append(locations[index:index + 4])
    if not rows:
        return None
    thumbs: list[list[Image.Image]] = []
    for row in rows:
        thumbs.append([])
        for path in row:
            with Image.open(path) as image:
                image = image.convert("RGB")
                thumbs[-1].append(image.resize((max(1, round(image.width * height / image.height)), height)))
    width = max(sum(t.width for t in row) + 8 * (len(row) + 1) for row in thumbs)
    sheet = Image.new("RGB", (width, len(thumbs) * (height + 8) + 8), (24, 24, 24))
    y = 8
    for row in thumbs:
        x = 8
        for thumb in row:
            sheet.paste(thumb, (x, y))
            x += thumb.width + 8
        y += height + 8
    sheet.save(output, quality=85)
    return output


def purge_unreadable(root: Path, *, paths=None) -> list[Path]:
    """Delete asset images that are missing bytes or are not images at all."""
    removed: list[Path] = []
    for path in sorted(set(root.rglob("*.jpeg") if paths is None else paths)):
        if not path.is_file():
            continue  # a missing selected image is built or awaited below
        if path.name.startswith("."):
            continue  # review markers and other dotfiles are not cards
        try:
            if path.stat().st_size < 20000:
                raise ValueError("too small to be a generated card")
            with Image.open(path) as image:
                image.load()
            continue
        except Exception as error:
            log(f"assets: discarding unreadable {path.parent.name}/{path.name} ({type(error).__name__})")
            for sidecar in (path, path.with_suffix(path.suffix + ".request.json"), path.with_suffix(path.suffix + ".task.json")):
                sidecar.unlink(missing_ok=True)
            removed.append(path)
    return removed


def broken_assets(ctx, manifest, *, paths=None) -> list[Path]:
    """Return asset images that are missing or that PIL cannot fully decode."""
    if paths is None:
        paths = [ctx.novel_dir / value for record in [*manifest.characters, *manifest.locations]
                 for value in (record.primary_image, getattr(record, "secondary_image", None)) if value]
    broken: list[Path] = []
    for path in dict.fromkeys(paths):
        if not path.is_file() or path.stat().st_size < 20000:
            broken.append(path)
            continue
        try:
            with Image.open(path) as image:
                image.load()
        except Exception:
            broken.append(path)
    return broken


