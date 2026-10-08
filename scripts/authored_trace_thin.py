"""Is this still what the author wrote?  Asked of every layer, for one chapter."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.planning.authored_trace import main

if __name__ == "__main__":
    raise SystemExit(main())
