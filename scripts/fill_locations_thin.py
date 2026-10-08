"""Give a bible's bare location names the description their empty-scene card needs."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.identity.location_design import main

if __name__ == "__main__":
    raise SystemExit(main())
