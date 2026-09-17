"""One finite remaining audit queue shared by local Qwen and Flash workers."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.audit_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
