"""Draw the variant cards phases.json names, one image each, through the renderer's own factory."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.assets.phase_cards import main

if __name__ == "__main__":
    raise SystemExit(main())
