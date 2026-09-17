"""Hand the automatic review's retake instructions to the lanes, without rendering anything here.

    apply_review_feedback.py --novel-dir outputs/X (--must-fix | --gate | --cards character_006,...)... [--episodes 12,48-60] [--pick N] [--apply]

thin_review writes a must_fix clip's instruction into episode_review.json `feedback`; the only thing a lane
acts on is review_feedback.json, and thin_batch writes that file only in --unattended mode, where it also
renders on the spot.  The conductor's review jobs run --review-only --no-render, so on 雾月 630 episodes
carried 848 instructions that nothing ever applied.  This writes the file and stops: the episode turns
`stale` (thin_runs.episode_status compares the file with the stamp in thin_media_report.json), a lane
re-renders it, and only the corrected clips regenerate - the correction changes their prompt, so their
request hash, while every other clip is served from the cache.  A new correction also resets the render
count, so an episode that used its runs gets them back for exactly this.

Each source is a switch and at least one is required - a card swap must not drag every must_fix along:
--must-fix takes the review's own instructions; --gate adds the standing instruction for clips that failed the speech gate (thin_media_report
gate_failed_clips) so a lane takes them again after their runs were used up.
--pick N chooses N episodes spread over the defect kinds (a pilot to measure the repair rate before the
whole book goes); --episodes names them.  Nothing is written without --apply."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.repair.feedback import main

if __name__ == "__main__":
    raise SystemExit(main())
