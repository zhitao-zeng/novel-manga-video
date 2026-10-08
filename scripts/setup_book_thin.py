"""Take a new novel from its text to a bible the planner can work from, in one run."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.planning.setup_book import main

if __name__ == "__main__":
    raise SystemExit(main())
