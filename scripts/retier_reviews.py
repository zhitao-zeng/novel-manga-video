"""Re-apply the current fix_tier() rules to reviews already on disk.

    retier_reviews.py --novel-dir outputs/X [--script-check] [--apply]

thin_review writes each failed clip's tier and, for must_fix, the retake instruction, at review time.  When
the tier rules change (2026-09-13: three regex leaks), the files keep the old verdicts and the delivery gate
keeps counting them.  This recomputes `tier` from the stored verdict with the rules as they are now, drops
the `feedback` entry of a clip that is no longer must_fix (keeps the existing text of one that still is),
rebuilds `flags`, and reports the difference.  The verdicts themselves are never changed; nothing is
re-judged.  Without --apply it only counts.  Each file it changes is backed up once as
episode_review.json.bak-retier.

--script-check asks, for every must_fix clip that was never checked, whether the flagged oddity is what the
book wrote (review_judges_thin.script_check: the clip's event line and source segments against the judge's
complaint); a scripted one drops to optional with the evidence stored, and its retake instruction goes.
The model is asked in the dry run too, so the count is real; only the writing waits for --apply."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.retier import main

if __name__ == "__main__":
    raise SystemExit(main())
