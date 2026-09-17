"""Record every card that an existing render used, so no automatic repair redraws it.

    mark_cards_in_use.py --novel-dir outputs/X [--apply]

render_clips_thin remembers the references of each clip that generated fine in
series_assets/.privacy_ok.json, and both the privacy repair and the card remediation leave those
cards alone.  Renders from before that bookkeeping (or made by another lane) are not in the file,
which is how 雾月's protagonist card was restyled on 2026-09-13 after 1,700 episodes had used it.
This walks the clip requests on disk and adds every referenced card path.  Without --apply it
only counts."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.assets.in_use import main

if __name__ == "__main__":
    raise SystemExit(main())
