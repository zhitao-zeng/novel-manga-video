"""Select technical re-renders without taking episodes owned by either repair lane."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.repair.filler import main

if __name__ == "__main__":
    raise SystemExit(main())
