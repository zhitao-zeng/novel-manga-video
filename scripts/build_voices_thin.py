"""Build each character's reference voice from the episodes already rendered."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.assets.voices import main

if __name__ == "__main__":
    raise SystemExit(main())
