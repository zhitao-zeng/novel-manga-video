"""Frame-level verification of rendered clips against the book: the "would a viewer notice" standard.

    verify_clips_thin.py --novel-dir outputs/X --mode candidates|sample|all|replay:<file>[:<mode>] [--sample 300]
                         [--workers 6] [--out <novel>/verify/verify.jsonl] [--judge-tag local] [--skip-episodes a,b]

Per clip: 5-6 frames + up to three cards + the passage, the planner's event line, the ledger's casting sheet and
(for candidates) the first judge's claim -> the model describes every person it sees, then answers five yes/no
questions (same person twice, species or gender wrong, action by the wrong person, an actor missing, a lead's
face swapped) and a verdict: obvious (a viewer who never saw the cards would notice), subtle (only a card
comparison shows it), fine.  Ghost text is recorded apart and does not drive the verdict.

Modes: candidates = the review's must_fix clips (with the judge's claim); sample = N random clips the review
passed (leak estimate); all = every clip with a take, except the candidates; replay = the clips another judge
verified (calibration).  Records are keyed by (episode, clip, video file): a rerun only verifies new takes.
The judge captures its QWEN38_LOCAL_* endpoint settings when constructed; each request uses that
instance configuration without changing another judge or the process environment.  雾月 2026-09-14: the judge confirmed on half of its must_fix
flags and missed 4% of what it passed; this is what made the final fix list."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.review.verify import main

if __name__ == "__main__":
    raise SystemExit(main())
