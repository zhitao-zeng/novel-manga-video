"""How the book actually refers to each character: the index every name lookup resolves through."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.identity.index import main

if __name__ == "__main__":
    raise SystemExit(main())
