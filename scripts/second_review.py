"""Ask what a viewer would notice, in two passes, and combine the answers."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.second_pass import main

if __name__ == "__main__":
    raise SystemExit(main())
