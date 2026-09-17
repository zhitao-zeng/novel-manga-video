"""Read the whole book before scripting any of it.

    story_pass_thin.py --novel-dir outputs/X [--chapters 1-3848] [--scan-workers 6] [--summary-workers 6] [--volume-size 50]

Scripting a chapter needs three things that only exist if the chapters before it
were read in order: a bible that already holds the people who will appear (with
the look from the chapter that introduced them), a recap of the last few
chapters, and the arc of the volumes before.  Planning is slow (minutes per
chapter) and can run in parallel blocks; reading is cheap (seconds) but its
result must be committed in order.  This is the reading pass, kept separate so
the planners can then run as many blocks as the GPUs allow with every chapter
properly warmed up.

Only the *commit* is sequential.  The model calls that do not depend on the
bible run ahead in pools:

  * scan: the chapter's proper names and locations (review_bible_thin.scan_chapter),
    several chapters at once
  * summary + hook per chapter into recap.json, order-free
  * ledger: the entity ledger's reading of the chapter (entity_ledger_thin),
    ahead in the same pool; resolved in chapter order before the commit, so
    the bible grows from the ledger's records instead of a second name scan
  * commit, in chapter order under the novel-wide lock: new names checked
    against the bible as it is *now*, descriptions written for the genuinely
    new ones (review_bible_thin.grow_bible with the scan handed in)
  * a volume summary every --volume-size chapters into volumes.json

Resumable: a chapter whose growth is recorded in bible_growth.json and whose
summary is in recap.json is skipped."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.planning.reading import main

if __name__ == "__main__":
    raise SystemExit(main())
