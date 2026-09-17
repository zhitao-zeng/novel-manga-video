"""Pack thin chapter shots into Seedance 2.5 clips and write staged prompts."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.packing.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
