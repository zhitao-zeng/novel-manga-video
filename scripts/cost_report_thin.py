"""Report observable generation usage, local/paid lanes and recorded final duration.

Existing task and repair records are counted once; missing history and prices remain explicit."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.reporting.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
