#!/usr/bin/env python3
"""Standalone chat-card command; production uses the shared media implementation."""
import argparse
import json
import sys
from pathlib import Path
from novel_manga.media.chat_card import build_segment, channels, compose_frame, draw_screen, load_avatars, windows

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--preview", action="store_true", help="also write a PNG of the last state")
    args = parser.parse_args()

    screen = json.loads((args.novel_dir / "chat_screen.json").read_text(encoding="utf-8")) if (args.novel_dir / "chat_screen.json").is_file() else {}
    plan = json.loads((args.episode_dir / "clip_plan.json").read_text(encoding="utf-8"))
    out_dir = args.episode_dir / "work" / "chat"
    out_dir.mkdir(parents=True, exist_ok=True)
    made = 0
    for clip in plan["clips"]:
        card = 0
        for run in channels(clip.get("chat_lines") or [], screen.get("self_name", "")):
            names = [str(m.get("speaker_name", "")) for m in run["messages"]] + [screen.get("self_name", ""), run["target"]]
            avatars = load_avatars(args.novel_dir, [n for n in names if n])
            title = run["target"] or screen.get("group_name", "群聊")
            for window in windows(len(run["messages"])):
                card += 1
                path, seconds = build_segment(
                    run["messages"], out_dir / f"{clip['clip_id']}_chat_{card:02d}.mp4",
                    title=title, self_name=screen.get("self_name", ""), group=not run["target"], avatars=avatars,
                    width=args.width, height=args.height, fps=args.fps, window=window,
                )
                print(f"{clip['clip_id']} chat {card}: messages {window[0] + 1}-{window[1]}, {seconds:.1f}s -> {path}")
                made += 1
                if args.preview:
                    frame = compose_frame(draw_screen(run["messages"], window[1], title=title, self_name=screen.get("self_name", ""), group=not run["target"], avatars=avatars), args.width, args.height, None)
                    frame.save(path.with_suffix(".png"))
    print(f"{made} chat cards")
    return 0
