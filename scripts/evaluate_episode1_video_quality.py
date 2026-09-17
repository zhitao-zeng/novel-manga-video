"""Run clip-level and director/VLM video gates for episode 1."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.video_quality import main

if __name__ == "__main__":
    raise SystemExit(main())
