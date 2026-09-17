"""How many corrected clips came back clean: the repair rate of a retake pilot.

    pilot_report.py --novel-dir outputs/X --targets tmp/fix/pilot_targets_0913.txt

For every episode in the list: the clips its review_feedback.json instructs, whether the episode has been
re-rendered (a final newer than the correction file) and re-reviewed (a review newer than that final),
and each instructed clip's severity and tier in the latest review.  A clip counts as repaired when it is
no longer must_fix; the two other columns keep "still must_fix" and "not yet re-reviewed" apart, so an
early reading is never mistaken for a low rate."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.reporting.pilot import main

if __name__ == "__main__":
    raise SystemExit(main())
