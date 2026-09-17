"""Write director corrections for the clips the second review flagged, ready to re-render.

A correction points at the fault in the reviewer's own words, then adds the standing
instruction for that kind of fault - the earlier A/B showed the reviewer's sentence beats a
tidied rewrite 6-7 to 2-8.  Writing the file is the whole trigger: it changes the prompt, so
the request hash changes, so that clip regenerates and every other clip in the episode stays
cached.

    repair_from_review.py <novel> [--votes 2] [--apply]"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.repair.from_review import main

if __name__ == "__main__":
    raise SystemExit(main())
