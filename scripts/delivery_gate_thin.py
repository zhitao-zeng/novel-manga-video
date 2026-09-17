"""One verdict per episode: is it deliverable, and if not, which gate stops it."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.production.delivery import main

if __name__ == "__main__":
    raise SystemExit(main())
