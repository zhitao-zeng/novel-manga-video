"""Set up a new novel for the thin pipeline: story bible + profile + grammar.

One LLM call (the repository's own ``BibleBuilder.build_bible``,
configured by ``NOVEL_LLM_*`` in ``.env``) extracts the reusable characters
and locations from the novel.  This script then writes everything the three
thin scripts read from ``outputs/<novel-id>/``:

    story_bible.json     characters, locations, visual_style (from the profile style)
    profile.json         {"style": "2d"|"3d", "frame": "9:16"|"16:9"}
    visual_grammar.json  copied from configs/templates, location_time keys pre-filled
    story_bible.md       the bible as a table, for the human review before any card is paid for
    novel.json           source path, sha256 and the chapter split (check it is the split you expect)

Example:
    .venv/bin/python scripts/build_bible_thin.py inputs/斗破苍穹-前10章.md --novel-id doupo-2d --title 斗破苍穹 --style 2d --frame 9:16"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.planning.initialize import main

if __name__ == "__main__":
    raise SystemExit(main())
