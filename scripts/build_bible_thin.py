"""Set up a new novel for the thin pipeline: story bible + profile + grammar."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.planning.initialize import main

if __name__ == "__main__":
    raise SystemExit(main())
