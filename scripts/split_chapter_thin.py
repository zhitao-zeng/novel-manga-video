"""Decide how many episodes a chapter is, and where they begin and end.

    python scripts/split_chapter_thin.py --novel-dir outputs/<书> --chapters 12
    python scripts/split_chapter_thin.py --novel-dir outputs/<书> --chapters 12 --count 3 --redo

Writes <书>/<书>_<n>/parts.json.  A chapter whose dialogue fits one episode is recorded as one part;
one that does not is cut where the story turns, by the planner's model, and checked.  Planning then
runs once per part (plan_chapter_thin --part k), and each part is an episode like any other.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_manga.application.planning.parts import decide_parts  # noqa: E402
from novel_manga.application.production.common import parse_chapters  # noqa: E402
from novel_manga.planning import parts as cp  # noqa: E402
from novel_manga.planning.authored_brief import chapter_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--chapters", required=True, help='例如 "12" 或 "1-100"')
    parser.add_argument("--count", type=int, default=0, help="强制分几集（默认按台词量算）")
    parser.add_argument("--redo", action="store_true", help="已有分集记录也重新分（已写好的分镜会对不上，慎用）")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    novel = json.loads((novel_dir / "novel.json").read_text(encoding="utf-8"))
    titles = {int(c["index"]): c.get("title", "") for c in novel.get("chapters", [])}
    for chapter in parse_chapters(args.chapters):
        episode_dir = novel_dir / cp.part_dir_name(novel_dir.name, chapter, None)
        if args.redo:
            (episode_dir / cp.PARTS_FILE).unlink(missing_ok=True)
        decide_parts(novel_dir, chapter, chapter_text(novel, chapter), titles.get(chapter, ""),
                     count=args.count or None, log=lambda line: print(line, flush=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
