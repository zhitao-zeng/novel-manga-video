"""Draw the variant cards phases.json names, one image each, through the renderer's own factory.

    build_phase_cards.py --novel-dir outputs/X [--only 沈玄川,阿曜] [--force] [--dry-run]

For every phase whose asset directory has no turnaround.jpeg: write spec.json (the base card's fields with
the phase's look laid over them, the prompt rebuilt the way build_selected() builds it) and draw the card
with ensure_card(), the same call the renderer makes for a base card.  Existing variant images are kept
unless --force, which moves them aside first.  The base card is never touched.  Prints one JSON line per
card, like build_cards_thin.py."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.assets.phase_cards import main

if __name__ == "__main__":
    raise SystemExit(main())
