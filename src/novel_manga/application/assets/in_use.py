#!/usr/bin/env python
"""Record every card that an existing render used, so no automatic repair redraws it.

    mark_cards_in_use.py --novel-dir outputs/X [--apply]

render_clips_thin remembers the references of each clip that generated fine in
series_assets/.privacy_ok.json, and both the privacy repair and the card remediation leave those
cards alone.  Renders from before that bookkeeping (or made by another lane) are not in the file,
which is how 雾月's protagonist card was restyled on 2026-09-13 after 1,700 episodes had used it.
This walks the clip requests on disk and adds every referenced card path.  Without --apply it
only counts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from novel_manga.media.asset_records import load_privacy_ok, record_privacy_ok


def referenced_paths(novel_dir: Path) -> set[str]:
    """Card paths as the runner records them: relative to the novel directory.  The request
    files hold absolute paths; a plan holds relative ones; both end up as the same key."""
    prefix = str(novel_dir) + "/"
    paths: set[str] = set()
    for request in novel_dir.glob(f"{novel_dir.name}_*/work/clips/clip_*/attempt_*/request.json"):
        try:
            refs = json.loads(request.read_text(encoding="utf-8")).get("references") or []
        except (OSError, ValueError):
            continue
        for ref in refs:
            rel = ref.get("path") if isinstance(ref, dict) else ref
            if not isinstance(rel, str):
                continue
            if rel.startswith(prefix):
                rel = rel[len(prefix):]
            if rel.startswith("series_assets/"):
                paths.add(rel)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    found = referenced_paths(novel_dir)
    known = load_privacy_ok(novel_dir)
    new = found - known
    print(f"{novel_dir.name}: 渲染请求引用的卡 {len(found)} 张，已在名单 {len(known & found)}，新增 {len(new)}"
          + ("" if args.apply else "（预演，加 --apply 才写）"))
    if args.apply and new:
        record_privacy_ok(novel_dir, new)
        print(f"  已写 {novel_dir / 'series_assets' / '.privacy_ok.json'}")
    return 0
