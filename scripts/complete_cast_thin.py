"""Put back the characters a storyboard's own shot descriptions name but its casts left out."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.planning.cast_completion import main

if __name__ == "__main__":
    raise SystemExit(main())
