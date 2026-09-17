"""Resume a book's pre-render source audit, targeted rewrite, cards and H3 prompts.

One subprocess owns one unrendered episode. Existing footage is never rewritten
by this preparation queue. Reports are text checks, not video-review verdicts."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.preparation.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
