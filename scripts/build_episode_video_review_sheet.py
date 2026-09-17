"""Create three-frame contact sheets and a director-review JSON skeleton."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.video_sheet import main

if __name__ == "__main__":
    raise SystemExit(main())
