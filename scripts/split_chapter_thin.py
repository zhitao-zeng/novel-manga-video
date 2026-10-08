"""Decide how many episodes a chapter is, and where they begin and end."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.planning.parts_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
