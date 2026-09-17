"""Split the over-long stages of clip plans packed before the packer did it, leaving every other clip as it is."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.packing.split import main

if __name__ == "__main__":
    raise SystemExit(main())
