"""Read a book with the lean pass and fold what it finds into the bible."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.planning.lean_bible import main

if __name__ == "__main__":
    raise SystemExit(main())
