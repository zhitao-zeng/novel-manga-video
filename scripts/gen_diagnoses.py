#!/usr/bin/env python3
"""Produce the story bible and per-episode chapter diagnosis for ftj-s1.

Extraction is what these planners are reliably good at, so the machine keeps
the factual layer while the creative layer (showrunner + shots) is written by
hand.  Nothing here touches media.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path("/mnt/disk1/zengzhitao/novel-manga-video")
sys.path.insert(0, str(ROOT / "src"))

from novel_manga.config import Settings
from novel_manga.ingest import read_novel
from novel_manga.providers import build_planner
from novel_manga.util import atomic_write_json

OUT = Path("/mnt/disk1/zengzhitao/tmp/ftj-s1-snapshot")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env(provider="phanrouter", output_root=str(ROOT / "outputs"), admission_mode="production")
    planner = build_planner(settings)
    novel = read_novel(ROOT / "outputs/inputs/ftj-s1-三章.txt", novel_id="ftj-s1", title="焚天纪")

    bible_path = OUT / "story_bible.json"
    if bible_path.exists():
        from novel_manga.models import StoryBible
        bible = StoryBible.model_validate_json(bible_path.read_text(encoding="utf-8"))
        print("bible: reused")
    else:
        bible = planner.build_bible(novel)
        atomic_write_json(bible_path, bible.model_dump(mode="json"))
        print("bible: generated")
    print("  characters:", [c.name for c in bible.characters])
    print("  locations :", list(bible.locations or []))
    print("  fingerprint:", bible.style_fingerprint)

    for episode in novel.episodes:
        target = OUT / f"chapter_diagnosis_ep{episode.index}.json"
        if target.exists():
            print(f"ep{episode.index}: diagnosis reused")
            continue
        diagnosis = planner._diagnose_episode(episode, bible, None)
        atomic_write_json(target, diagnosis.model_dump(mode="json"))
        print(f"ep{episode.index}: {len(diagnosis.events)} events, density={diagnosis.density}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
