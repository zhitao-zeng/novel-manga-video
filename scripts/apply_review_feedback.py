"""Hand the automatic review's retake instructions to the lanes, without rendering anything here."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.repair.feedback import main

if __name__ == "__main__":
    raise SystemExit(main())
