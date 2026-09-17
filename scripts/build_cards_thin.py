"""Build (and optionally review + fix) a few asset cards, one process per job.

    build_cards_thin.py --novel-dir outputs/X --assets character_041,location_012 [--review] [--tier fast]

Meant to be run many at a time by ``thin_batch.py``'s card factory: each job
takes a per-asset file lock, builds the card(s) it was given through the same
factory the renderer uses (so prompts and ids are identical), then - with
``--review`` - judges them with the VLM and applies the one bounded fix
(stylized redraw for near-photoreal cards, empty-scene rebuild for location
cards with people).  Prints one JSON line per asset."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.assets.cards import main

if __name__ == "__main__":
    raise SystemExit(main())
