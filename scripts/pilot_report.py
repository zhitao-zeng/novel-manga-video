"""How many corrected clips came back clean: the repair rate of a retake pilot."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.reporting.pilot import main

if __name__ == "__main__":
    raise SystemExit(main())
