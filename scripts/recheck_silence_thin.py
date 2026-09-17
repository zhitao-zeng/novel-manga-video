"""Recheck existing thin finals' silence gates, without rendering or changing any video.

    PYTHONPATH=src:scripts .venv/bin/python scripts/recheck_silence_thin.py --novel-dir outputs/wuyue --apply

Only current previews that failed silence checks are considered. Other QC and speech verdicts are retained.
Original reports are saved under the run's report directory before applying an update."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.repair.recheck_silence import main

if __name__ == "__main__":
    raise SystemExit(main())
