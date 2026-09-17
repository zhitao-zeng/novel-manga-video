"""Prepare one owned episode for structural, technical or residual recovery."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.repair.recovery import main

if __name__ == "__main__":
    raise SystemExit(main())
