"""Frame-level verification of rendered clips against the book: the would a viewer notice standard."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.verify import main

if __name__ == "__main__":
    raise SystemExit(main())
