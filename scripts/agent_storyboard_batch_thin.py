"""A range of chapters through the sandbox: storyboard, accept what needs no choosing, bind, check."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.agents.storyboard_batch_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
