"""Extract reusable single-view assets from approved/candidate 3D review cards."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.assets.extract_views import main

if __name__ == "__main__":
    raise SystemExit(main())
