"""List the models of OpenAI-compatible endpoints."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.planning.endpoint_probe import main

if __name__ == "__main__":
    raise SystemExit(main())
