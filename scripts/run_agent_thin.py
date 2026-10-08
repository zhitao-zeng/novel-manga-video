"""Run one agent in the sandbox container, from the command line."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.agents.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
