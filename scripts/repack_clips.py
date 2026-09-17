"""Re-pack planned episodes to a different clip length, without planning anything again."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.packing.repack import main

if __name__ == "__main__":
    raise SystemExit(main())
