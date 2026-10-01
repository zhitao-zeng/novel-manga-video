"""Run the isolated episode agent using the application's ordinary CLI."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from novel_manga.application.agents.episode.cli import main


if __name__ == '__main__':
    raise SystemExit(main())
