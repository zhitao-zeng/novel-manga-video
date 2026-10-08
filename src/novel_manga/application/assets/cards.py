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
import shutil
import sys
import time
from pathlib import Path

from novel_manga.media.policy import RATE_LIMIT_RE
from novel_manga.media.asset_builder import FramedAssetFactory, load_location_time
from novel_manga.media.asset_policy import ModerationRejected
from novel_manga.media.asset_style import AssetStyle, image_backend
from novel_manga.media.adapters import FramedPhanRouter
from novel_manga.media.common import log
from novel_manga.application.profiles import effective_profile, frame_spec, load_genre, load_profile, load_style, project_root, style_names, styled_bible
from novel_manga.util import load_dotenv
from novel_manga.util import atomic_write_json

from novel_manga.config import Settings  # noqa: E402
from novel_manga.models.bible import StoryBible
from dataclasses import replace as dc_replace  # noqa: E402

IMAGE_BACKOFF = (20, 40, 60, 90, 120)


def split_asset_ids(text: str) -> tuple[set[str], set[str], set[str]]:
    """--assets 的 id 按前缀分道：character_/location_/prop_。"""
    ids = {a.strip() for a in text.split(",") if a.strip()}
    return ({a for a in ids if a.startswith("character_")},
            {a for a in ids if a.startswith("location_")},
            {a for a in ids if a.startswith("prop_")})


def build_with_backoff(factory, root: Path, bible: StoryBible, characters: set[str], locations: set[str], profile: dict | None = None, props: set[str] | None = None,
                       *, redraw: bool = False, review_candidate=None):
    for wait in (*IMAGE_BACKOFF, None):
        try:
            return factory.build_selected(root, bible, characters, locations,
                                          expressions=effective_profile(profile or {})["expressions"], prop_ids=props or set(),
                                          **({"redraw": True} if redraw else {}),
                                          **({"review_candidate": review_candidate} if review_candidate is not None else {}))
        except ModerationRejected:
            raise
        except RuntimeError as error:
            if wait is None or not RATE_LIMIT_RE.search(str(error)):
                raise
            log(f"image service throttled ({str(error)[:80]}); retrying in {wait}s")
            time.sleep(wait)


def review_existing(novel_dir,ids):
    from novel_manga.application.review.cards import card_verdict_current,review_cards
    from novel_manga.resource_waits import QueueWait
    root=novel_dir/'series_assets/.locks';root.mkdir(parents=True,exist_ok=True)
    types_path=novel_dir/'entity/types.json'
    types=json.loads(types_path.read_text()) if types_path.exists() else {}
    okay=True
    for ident in ids:
        with QueueWait('shared_asset') as queue,(root/(ident+'.lock')).open('a') as lock:
            queue.begin();fcntl.flock(lock,fcntl.LOCK_EX);queue.finish()
            review=card_verdict_current(novel_dir,ident)
            if review is None:review=review_cards(novel_dir,only_ids={ident})
            entries=review.get('characters',{}) | review.get('locations',{})
            if ident not in entries:raise ValueError(f'no existing image review produced for {ident}')
            mismatch=ident.startswith('character_') and types.get(entries[ident].get('name'),{}).get('kind')=='object'
            okay=okay and not mismatch
            print(json.dumps({'asset_id':ident,'status':'entity_type_mismatch' if mismatch else 'reviewed',
                              'flags':review['flags']},ensure_ascii=False),flush=True)
    return 0 if okay else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--assets", required=True, help="comma-separated asset ids, e.g. character_041,location_012")
    parser.add_argument("--redraw", action="store_true",
                        help="generate new cards only for --assets, then archive and replace their prior views")
    parser.add_argument("--review", action="store_true", help="judge the cards and apply the one bounded fix")
    parser.add_argument("--review-only",action="store_true",help="review existing images without drawing or remediation")
    parser.add_argument('--fresh-review',action='store_true',help='recheck flagged shared images before the existing one-fix limit')
    parser.add_argument("--style", choices=tuple(style_names()))
    parser.add_argument("--frame", choices=("9:16", "16:9"))
    parser.add_argument("--tier", choices=("quality", "fast"))
    args = parser.parse_args()
    if args.redraw and args.review_only:
        parser.error("--redraw cannot be combined with --review-only")
    ids = list(dict.fromkeys(a.strip() for a in args.assets.split(",") if a.strip()))
    characters, locations, props = split_asset_ids(args.assets)
    if not ids or set(ids) != characters | locations | props:
        parser.error("--assets must explicitly select character_, location_ or prop_ asset ids")

    # As build_phase_cards does.  Without it this entry only runs when the caller has
    # already exported the keys, and fails at validate() with a missing PHANROUTER_API_KEY
    # however the cards were actually going to be drawn.  setdefault, so an exported value
    # still wins.
    load_dotenv(project_root() / ".env")
    novel_dir = args.novel_dir.resolve()
    if args.review_only:
        return review_existing(novel_dir,[a.strip() for a in args.assets.split(',') if a.strip()])
    profile = load_profile(novel_dir, style=args.style, frame=args.frame, tier=args.tier)
    frame = frame_spec(profile)
    settings = Settings.from_env(provider="phanrouter", output_root=novel_dir.parent, admission_mode="preview")
    asset_style = AssetStyle.for_genre(load_genre(profile), frame_text=frame["text"],
                                       style=load_style(profile, novel_dir),
                                       backend=image_backend(settings))
    settings = dc_replace(settings, width=frame["width"], height=frame["height"])
    bible = StoryBible.model_validate_json((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    if (novel_dir / "profile.json").is_file():
        bible = styled_bible(bible, profile, novel_dir)
    provider = FramedPhanRouter(settings, frame)
    factory = FramedAssetFactory(settings, provider, style=asset_style,
                                 location_time=load_location_time(novel_dir))
    root = novel_dir / "series_assets"
    (root / ".locks").mkdir(parents=True, exist_ok=True)

    def review_candidate(candidate_novel_dir: Path, asset_id: str) -> dict:
        """Review fresh selected views against the current Bible before publishing."""
        from novel_manga.application.review.cards import review_cards
        atomic_write_json(candidate_novel_dir / "story_bible.json", bible.model_dump(mode="json"))
        for filename in ("profile.json", "style.json", "visual_grammar.json"):
            source = novel_dir / filename
            if source.is_file():
                shutil.copy2(source, candidate_novel_dir / filename)
        return review_cards(candidate_novel_dir, only_ids={asset_id})

    results = {}
    type_path = novel_dir / 'entity/types.json'
    types = json.loads(type_path.read_text()) if type_path.is_file() else {}
    for asset_id in ids:
        lock_path = root / ".locks" / f"{asset_id}.lock"
        from novel_manga.resource_waits import QueueWait
        with QueueWait("shared_asset") as queue, open(lock_path, "w") as lock:
            queue.begin()
            fcntl.flock(lock, fcntl.LOCK_EX)  # another job or a renderer may be building the same card
            queue.finish()
            started = time.monotonic()
            try:
                if asset_id in locations:
                    from novel_manga.application.identity.reading_locations import ensure_designs
                    ensure_designs(novel_dir, {asset_id})
                    bible = StoryBible.model_validate_json((novel_dir/'story_bible.json').read_text(encoding='utf-8'))
                    if (novel_dir/'profile.json').is_file():
                        bible = styled_bible(bible, profile, novel_dir)
                build_with_backoff(factory, root, bible, {asset_id} & characters, {asset_id} & locations, profile,
                                   {asset_id} & props, redraw=args.redraw,
                                   review_candidate=review_candidate if args.redraw and args.review else None)
                status = "built"
            except ModerationRejected as error:
                status = f"moderation: {str(error)[:120]}"
            except Exception as error:  # noqa: BLE001 - one bad card must not kill the worker
                status = f"error: {type(error).__name__}: {str(error)[:120]}"
            row = {"asset_id": asset_id, "status": status, "seconds": round(time.monotonic() - started, 1), "flags": []}
            if args.review and status == "built":
                from novel_manga.application.review.cards import card_verdict_current, remediate_cards, review_cards
                cached = (card_verdict_current(novel_dir, asset_id) if args.redraw
                          else None if args.fresh_review else card_verdict_current(novel_dir, asset_id))
                # judged before and unchanged since: a clean verdict stands, a
                # flagged one goes straight to its one fix without re-judging
                review = cached if cached is not None else review_cards(novel_dir, only_ids={asset_id})
                if review["flags"]:
                    fixes = remediate_cards(novel_dir, review)
                    if fixes["deleted"]:
                        build_with_backoff(factory, root, bible, {asset_id} & characters, {asset_id} & locations,
                                           profile, {asset_id} & props)
                    if fixes['deleted'] or fixes['stylized']:
                        review = review_cards(novel_dir, only_ids={asset_id})
                    row["fixes"] = fixes["stylized"] + fixes["deleted"]
                elif cached is not None:
                    row["review"] = "candidate_passed" if args.redraw else "cached"
                row["flags"] = review["flags"]
                entry = (review.get('characters', {}) | review.get('locations', {})).get(asset_id, {})
                if asset_id.startswith('character_') and types.get(entry.get('name'), {}).get('kind') == 'object':
                    row['status'] = 'entity_type_mismatch'
            results[asset_id] = row
            print(json.dumps(row, ensure_ascii=False), flush=True)
    return 0 if all(r["status"] == "built" for r in results.values()) else 2
