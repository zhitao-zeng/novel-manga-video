"""Batch media command; generation, cache, analysis and postprocessing have shared owners."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.rendering.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
