"""Ask the sandbox for a chapter's storyboard, see what came back, and choose one."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.agents.storyboard_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
