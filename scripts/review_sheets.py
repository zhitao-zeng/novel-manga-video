"""Contact sheets for the clips the second review flagged, so a person can check before paying."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.sheets import main

if __name__ == "__main__":
    raise SystemExit(main())
