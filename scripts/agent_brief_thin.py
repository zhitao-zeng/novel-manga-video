"""Write the input directory an authoring agent reads, from the book's own bible."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.agents.brief_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
