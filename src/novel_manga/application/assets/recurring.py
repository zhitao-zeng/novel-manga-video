#!/usr/bin/env python
"""Cards for named characters who keep coming back without one.

    recurring_cards_thin.py --novel-dir outputs/X [--min-episodes 2] [--build]

Cards are normally built only for the characters an episode puts on screen; a
character who only ever speaks in the group chat, or off screen, is never
referenced and so never gets one - and then the chat card shows a coloured
initial for them and, if they ever do step into frame, the video model invents a
face.  This lists every named character (anonymous 无名 roles excluded) who is
in the bible, appears in at least --min-episodes episodes by any route (on
screen, chat, off screen) and has no card, and with --build renders their
turnaround card through build_cards_thin.py.  thin_batch.py runs the same rule
after each episode is planned.
"""
from __future__ import annotations
from novel_manga.application.configuration import project_root

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

SCRIPTS = (project_root() / 'scripts')


def appearances(novel_dir: Path) -> dict[str, set[str]]:
    """name -> episodes the character appears in, on screen or as any kind of speaker."""
    seen: dict[str, set[str]] = defaultdict(set)
    for directory in sorted(d for d in novel_dir.iterdir() if d.is_dir() and d.name.startswith(novel_dir.name + "_")):
        script = directory / "chapter_script.json"
        if not script.is_file():
            continue
        try:
            shots = json.loads(script.read_text(encoding="utf-8")).get("shots", [])
        except (OSError, ValueError):
            continue
        for shot in shots:
            for name in shot.get("characters", []) or []:
                seen[str(name)].add(directory.name)
            for turn in shot.get("turns", []) or []:
                if turn.get("speaker_name"):
                    seen[str(turn["speaker_name"])].add(directory.name)
    return seen


def recurring_without_cards(novel_dir: Path, min_episodes: int = 2) -> list[tuple[str, str, int]]:
    """[(name, asset_id, episode_count)] for characters that qualify for a card."""
    bible_path = novel_dir / "story_bible.json"
    if not bible_path.is_file():
        return []
    bible = json.loads(bible_path.read_text(encoding="utf-8"))
    index = {character["name"]: f"character_{position:03d}" for position, character in enumerate(bible.get("characters", []), start=1)}
    manifest_path = novel_dir / "series_assets" / "manifest.json"
    carded = set()
    if manifest_path.is_file():
        carded = {row.get("name") for row in json.loads(manifest_path.read_text(encoding="utf-8")).get("characters", [])}
    rows = []
    for name, episodes in appearances(novel_dir).items():
        if name.startswith("无名") or name not in index or name in carded or len(episodes) < min_episodes:
            continue
        rows.append((name, index[name], len(episodes)))
    return sorted(rows, key=lambda row: -row[2])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--min-episodes", type=int, default=2)
    parser.add_argument("--build", action="store_true", help="render the missing cards now (one image generation each)")
    parser.add_argument("--tier", choices=("quality", "fast"))
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    rows = recurring_without_cards(novel_dir, args.min_episodes)
    if not rows:
        print("没有需要补卡的角色")
        return 0
    for name, asset_id, count in rows:
        print(f"{name} ({asset_id}) 出现 {count} 集，无卡")
    if not args.build:
        print(f"共 {len(rows)} 个；加 --build 建卡")
        return 0
    command = [sys.executable, str(SCRIPTS / "build_cards_thin.py"), "--novel-dir", str(novel_dir), "--assets", ",".join(asset_id for _, asset_id, _ in rows)]
    if args.tier:
        command += ["--tier", args.tier]
    return subprocess.run(command).returncode
