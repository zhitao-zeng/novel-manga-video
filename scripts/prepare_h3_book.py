"""Resume a book's pre-render source audit, targeted rewrite, cards and H3 prompts."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.preparation.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
