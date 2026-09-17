"""One verdict per episode: is it deliverable, and if not, which gate stops it.

    delivery_gate_thin.py --novel-dir outputs/X [--recent-min 3] [--quiet]

Three gates.  An episode is deliverable when it passes the first two; the third is reported
but does not block (shadow mode) until its false-positive rate has been measured.

    技术  thin_runs.episode_status() == "done": the final exists, the media checks passed and
          no clip failed the speech gate.  What thin_batch and the conductor already act on.
    审查  the latest episode_review.json is newer than the final, covers every video clip of
          the current plan, and carries no retake instruction.  thin_review writes `feedback`
          only for must_fix verdicts, so "no feedback" is "nothing a viewer would notice".
          (2026-09-12: 雾月 had 630 such episodes and no review_feedback.json anywhere - the
          instructions were computed and never applied; this gate makes that visible.)
    剧本  no cast member of the clip plan is absent from the chapter text under every surface
          form (canonical name, the part before the dot, every alias).  Measured with the
          bible's own alias table; names with no surface form of two or more characters
          (雾月's "神") cannot be measured and are skipped, not counted.

Writes outputs/X/delivery.json - the counts, the reason combinations and one row per
episode - for the status board and for anyone asking "is this novel done"."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.production.delivery import main

if __name__ == "__main__":
    raise SystemExit(main())
