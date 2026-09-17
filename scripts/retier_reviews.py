"""Re-apply the current fix_tier() rules to reviews already on disk."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.retier import main

if __name__ == "__main__":
    raise SystemExit(main())
