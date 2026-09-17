"""Build (and optionally review + fix) a few asset cards, one process per job."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.assets.cards import main

if __name__ == "__main__":
    raise SystemExit(main())
