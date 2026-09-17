"""Mark clips for re-rendering after a character card is replaced.

A clip is reused when its request matches, and requests written before 2026-09-10 record only
the reference paths, so replacing a card leaves every existing clip untouched.  Deciding which
of them to redo is a judgement about money, not something to do automatically: this moves the
cached attempt of the chosen clips aside, and the next render regenerates exactly those.

    invalidate_cards.py <novel> <asset_id>[,<asset_id>...] [--flagged-only] [--apply]

Without --apply it only reports.  The moved attempt keeps its files under
work/clips/<clip>/superseded-<timestamp>/, outside the attempt_* names the runner scans, so
nothing is lost and nothing is silently reused."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.assets.invalidate import main

if __name__ == "__main__":
    raise SystemExit(main())
