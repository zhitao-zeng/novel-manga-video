#!/usr/bin/env python
"""Build (and optionally review + fix) a few asset cards, one process per job.

    build_cards_thin.py --novel-dir outputs/X --assets character_041,location_012 [--review] [--tier fast]

Meant to be run many at a time by ``thin_batch.py``'s card factory: each job
takes a per-asset file lock, builds the card(s) it was given through the same
factory the renderer uses (so prompts and ids are identical), then - with
``--review`` - judges them with the VLM and applies the one bounded fix
(stylized redraw for near-photoreal cards, empty-scene rebuild for location
cards with people).  Prints one JSON line per asset.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_clips_thin import RATE_LIMIT_RE, FramedAssetFactory, FramedPhanRouter, ModerationRejected, log  # noqa: E402
from thin_profile import frame_spec, is_fast, load_profile, styled_bible  # noqa: E402

from novel_manga.config import Settings  # noqa: E402
from novel_manga.models import StoryBible  # noqa: E402
from dataclasses import replace as dc_replace  # noqa: E402

IMAGE_BACKOFF = (20, 40, 60, 90, 120)


def build_with_backoff(factory, root: Path, bible: StoryBible, characters: set[str], locations: set[str], profile: dict | None = None):
    for wait in (*IMAGE_BACKOFF, None):
        try:
            return factory.build_selected(root, bible, characters, locations, expressions=not is_fast(profile))
        except ModerationRejected:
            raise
        except RuntimeError as error:
            if wait is None or not RATE_LIMIT_RE.search(str(error)):
                raise
            log(f"image service throttled ({str(error)[:80]}); retrying in {wait}s")
            time.sleep(wait)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--assets", required=True, help="comma-separated asset ids, e.g. character_041,location_012")
    parser.add_argument("--review", action="store_true", help="judge the cards and apply the one bounded fix")
    parser.add_argument("--style", choices=("2d", "3d"))
    parser.add_argument("--frame", choices=("9:16", "16:9"))
    parser.add_argument("--tier", choices=("quality", "fast"))
    args = parser.parse_args()

    novel_dir = args.novel_dir.resolve()
    profile = load_profile(novel_dir, style=args.style, frame=args.frame, tier=args.tier)
    frame = frame_spec(profile)
    settings = Settings.from_env(provider="phanrouter", output_root=novel_dir.parent, admission_mode="preview")
    settings = dc_replace(settings, width=frame["width"], height=frame["height"])
    bible = StoryBible.model_validate_json((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    if (novel_dir / "profile.json").is_file():
        bible = styled_bible(bible, profile)
    provider = FramedPhanRouter(settings, frame)
    factory = FramedAssetFactory(settings, provider)
    factory.frame_text = frame["text"]
    root = novel_dir / "series_assets"
    (root / ".locks").mkdir(parents=True, exist_ok=True)

    ids = [a.strip() for a in args.assets.split(",") if a.strip()]
    characters = {a for a in ids if a.startswith("character_")}
    locations = {a for a in ids if a.startswith("location_")}
    results = {}
    for asset_id in ids:
        lock_path = root / ".locks" / f"{asset_id}.lock"
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)  # another job or a renderer may be building the same card
            started = time.monotonic()
            try:
                build_with_backoff(factory, root, bible, {asset_id} & characters, {asset_id} & locations, profile)
                status = "built"
            except ModerationRejected as error:
                status = f"moderation: {str(error)[:120]}"
            except Exception as error:  # noqa: BLE001 - one bad card must not kill the worker
                status = f"error: {type(error).__name__}: {str(error)[:120]}"
            row = {"asset_id": asset_id, "status": status, "seconds": round(time.monotonic() - started, 1), "flags": []}
            if args.review and status == "built":
                from thin_review import remediate_cards, review_cards
                review = review_cards(novel_dir, only_ids={asset_id})
                if review["flags"]:
                    fixes = remediate_cards(novel_dir, review)
                    if fixes["deleted"]:
                        build_with_backoff(factory, root, bible, {asset_id} & characters, {asset_id} & locations, profile)
                    review = review_cards(novel_dir, only_ids={asset_id})
                    row["fixes"] = fixes["stylized"] + fixes["deleted"]
                row["flags"] = review["flags"]
            results[asset_id] = row
            print(json.dumps(row, ensure_ascii=False), flush=True)
    return 0 if all(r["status"] == "built" for r in results.values()) else 2


if __name__ == "__main__":
    sys.exit(main())
