"""Promote the user-approved 3D card library without replacing the 2D baseline."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.assets.promote import main

if __name__ == "__main__":
    raise SystemExit(main())
