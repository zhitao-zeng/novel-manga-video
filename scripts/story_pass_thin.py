"""Read the whole book before scripting any of it."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.planning.reading import main

if __name__ == "__main__":
    raise SystemExit(main())
