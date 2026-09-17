"""Contact sheets for the clips the second review flagged, so a person can check before paying.

The frames come from second_review.frames_of, not from a sampling of this script's own: when
they differed, I "verified" a verdict against frames the judge never saw and read the wrong
conclusion off it.  Whatever the judge looked at is what goes on the sheet.

    review_sheets.py <novel> [--votes 2] [--limit 40] [--out DIR]"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.sheets import main

if __name__ == "__main__":
    raise SystemExit(main())
