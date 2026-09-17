"""The verification gate: only clips the frame-level verifier calls obvious stay must_fix.

    verify_gate_thin.py --novel-dir outputs/X --episodes 1-2043 [--apply] [--verify-file <novel>/verify/verify.jsonl]

For every must_fix clip of the episodes: a verification of the very same take is looked up (verify_clips_thin
records); a clip without one is verified now.  obvious -> stays must_fix (annotated `verified`); anything else ->
tier optional, its retake instruction moved from `feedback` to `feedback_cleared`, so apply_review_feedback and
repair_clips_thin no longer touch it.  A clip the judge passed but the verifier saw an obvious error in (sample or
all mode) is promoted: must_fix, story_ok false, story_issue and feedback from the evidence.  A must_fix carrying
`technical` (black frames) or written by the verify-mode judge itself (`verify`) is never cleared here.
episode_review.json is backed up once as episode_review.json.bak-gate before the first change."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.gate import main

if __name__ == "__main__":
    raise SystemExit(main())
