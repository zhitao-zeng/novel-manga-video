#!/usr/bin/env python3
"""Pack thin chapter shots into Seedance 2.5 clips and write staged prompts.

Reads <episode_dir>/chapter_script.json (from plan_chapter_thin.py) plus the
StoryBible.  Consecutive shots are packed into clips of at most 30 seconds
and at most 6 stages; a clip is cut on a location change, when it would
exceed 30 seconds, or, once it is already long enough, when the chapter
segment changes.  Each clip gets one prompt in the official Seedance 2.5
layout: 【生成目标】, per-material bindings (用于 / 不采用), 【阶段n】 with
开始时 / 主要事件 / 声音 / 结束时, then style, camera, sound and 【保持一致】.
Title-card shots become separate card segments rendered in post.
Writes clip_plan.json and clip_plan.md.  No model call, no remote call.
"""
from __future__ import annotations
from novel_manga.application.configuration import project_root

import argparse
import sys
from pathlib import Path
ROOT = project_root()
from novel_manga.application.packing.flow import run

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--bible", type=Path, required=True)
    parser.add_argument("--grammar", type=Path, help="visual_grammar.json; defaults to <novel dir>/visual_grammar.json when present")
    parser.add_argument("--style", choices=("2d", "3d"), help="override profile.json style")
    parser.add_argument("--frame", choices=("9:16", "16:9"), help="override profile.json frame")
    parser.add_argument("--tier", choices=("quality", "fast"), help="override profile.json tier")
    args = parser.parse_args()
    return run(args)
