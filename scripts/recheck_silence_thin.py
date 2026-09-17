"""Recheck existing thin finals' silence gates, without rendering or changing any video."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.repair.recheck_silence import main

if __name__ == "__main__":
    raise SystemExit(main())
