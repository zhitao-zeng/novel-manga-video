#!/usr/bin/env python
"""Batch review commands: bible, cards, episode, volume and grow."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from novel_manga.application.review.bible import grow_bible, review_bible, summarize_volume
from novel_manga.application.review.cards import review_cards
from novel_manga.application.review.episode import review_episode

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("bible"); p.add_argument("--novel-dir", type=Path, required=True); p.add_argument("--source", type=Path); p.add_argument("--fill", action="store_true")
    p = sub.add_parser("cards"); p.add_argument("--novel-dir", type=Path, required=True); p.add_argument("--include-backups", action="store_true")
    p = sub.add_parser("episode"); p.add_argument("--episode-dir", type=Path, required=True); p.add_argument("--video-name", default="clip.mp4")
    p = sub.add_parser("volume"); p.add_argument("--novel-dir", type=Path, required=True); p.add_argument("--first", type=int, required=True); p.add_argument("--last", type=int, required=True)
    p = sub.add_parser("grow"); p.add_argument("--novel-dir", type=Path, required=True); p.add_argument("--source", type=Path); p.add_argument("--chapter", type=int, required=True)
    args = parser.parse_args()
    if args.command == "grow":
        from novel_manga.ingest import read_novel
        novel_dir = args.novel_dir.resolve()
        source = args.source or Path(json.loads((novel_dir / "novel.json").read_text(encoding="utf-8"))["source"])
        novel = read_novel(Path(source).resolve(), novel_id=novel_dir.name)
        print(json.dumps(grow_bible(novel_dir, novel.episodes[args.chapter - 1].source_text, args.chapter), ensure_ascii=False, indent=1))
        return 0
    if args.command == "volume":
        print(json.dumps(summarize_volume(args.novel_dir.resolve(), args.first, args.last), ensure_ascii=False, indent=1))
        return 0
    if args.command == "bible":
        novel_dir = args.novel_dir.resolve()
        source = args.source or Path(json.loads((novel_dir / "novel.json").read_text(encoding="utf-8"))["source"])
        report = review_bible(novel_dir, Path(source).resolve(), args.fill)
        print(json.dumps({"missing": report["missing"], "filled": report["filled"]}, ensure_ascii=False, indent=1))
    elif args.command == "cards":
        report = review_cards(args.novel_dir.resolve(), args.include_backups)
        print(json.dumps({"flags": report["flags"]}, ensure_ascii=False, indent=1))
    else:
        report = review_episode(args.episode_dir.resolve(), args.video_name)
        print(json.dumps({"flags": report["flags"], "feedback": report["feedback"]}, ensure_ascii=False, indent=1))
    return 0
