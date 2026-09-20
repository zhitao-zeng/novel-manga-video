#!/usr/bin/env python3
"""Plan a chapter through the existing outline, screenplay and bounded repair flow."""
from __future__ import annotations
import argparse
import os
import sys
import json
from pathlib import Path
from novel_manga.application.profiles import style_names
from novel_manga.planning.context import PlannerContext
from novel_manga.application.planning.requests import qwen_default
from novel_manga.application.planning.flow import run, PlanningInputError
from novel_manga.planning.methods import METHODS

def main(*, context: PlannerContext | None = None) -> int:
    ctx = context or PlannerContext.from_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", nargs="?")
    parser.add_argument("--novel-id")
    parser.add_argument("--title")
    parser.add_argument("--episode-index", type=int, default=1)
    parser.add_argument("--bible", help="existing story_bible.json to reuse")
    parser.add_argument("--output-root", default="outputs")
    parser.add_argument("--base-url", default=qwen_default(), help="one Qwen endpoint; the default spreads over every QWEN38_LOCAL_BASE_URL entry")
    parser.add_argument("--model", default=os.getenv("QWEN38_LOCAL_MODEL", "Qwen3.8-27B-Project"))
    # A healthy clip plan is 4.5-6.5K tokens.  Constrained decoding can derail
    # into endless whitespace; a tight cap turns that into a fast, cheap redo.
    parser.add_argument("--max-tokens", type=int, default=int(os.environ.get("NOVEL_PLAN_MAX_TOKENS") or (12000 if ctx.short_clips else 9000)),
                        help="completion budget; the 15 s mode writes 6-8 clips and needs more room (chapter 381 was cut off three times at 9000)")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--max-redo", type=int, default=1)
    parser.add_argument("--min-seconds", type=float, default=0.0, help="reject a plan shorter than this (drives the redo)")
    parser.add_argument("--max-seconds", type=float, help="explicit episode ceiling; story methods otherwise use a duration target, not a hard 105s limit")
    parser.add_argument("--notes", default="", help="director feedback injected into this chapter's request")
    parser.add_argument("--grammar", type=Path, help="visual_grammar.json; defaults to <output-root>/<novel-id>/visual_grammar.json when present")
    parser.add_argument("--style", choices=tuple(style_names()), help="override profile.json style")
    parser.add_argument("--frame", choices=("9:16", "16:9"), help="override profile.json frame")
    parser.add_argument("--dry-run", action="store_true", help="build segments and request only")
    parser.add_argument("--replay", type=Path, help="validate an existing raw response instead of calling the model")
    parser.add_argument("--merge", type=int, default=1, help="chapters per episode: episode k covers chapters (k-1)*N+1..k*N")
    parser.add_argument("--tier", choices=("quality", "fast"), help="override profile.json tier")
    parser.add_argument("--outline-mode", choices=("coverage", "story"), default="coverage", help="first-pass planning; story is the experimental causal/scene outline")
    parser.add_argument("--outline-tokens", type=int, help="first-pass budget: default 4096 for an outline, 8192 for a method's scene screenplay")
    parser.add_argument("--seed", type=int, help="optional model sampling seed for matched planning experiments")
    parser.add_argument("--story-method", choices=("default", *METHODS),
                        help="local writing/directing method; otherwise inherit profile.json")
    parser.add_argument("--list-methods", action="store_true", help="list local story methods without loading a novel")
    parser.add_argument("--storyboard-sheet", help="faithfully import this XLSX sheet as an authored draft; no model calls")
    args = parser.parse_args()
    if args.list_methods:
        print(json.dumps([m.describe() for m in METHODS.values()], ensure_ascii=False, indent=2))
        return 0
    if args.storyboard_sheet is not None:
        if not args.source or not args.novel_id or not args.style or not args.frame:
            parser.error("分镜导入需要 source、--novel-id、--style 和 --frame")
        if args.replay or args.dry_run or args.story_method or args.merge != 1:
            parser.error("分镜导入不执行改编、回放或合章，请移除相应参数")
        if args.episode_index < 1:
            parser.error("--episode-index must be positive")
        from novel_manga.application.planning.storyboard import run as import_storyboard
        try:
            return import_storyboard(args)
        except PlanningInputError as error:
            parser.error(str(error))
    if not args.source or not args.novel_id or not args.bible:
        parser.error("source, --novel-id and --bible are required for chapter planning")
    if args.outline_tokens is not None and args.outline_tokens <= 0:
        parser.error("--outline-tokens must be positive")
    if args.max_seconds is not None and (args.max_seconds <= 0 or args.max_seconds < args.min_seconds):
        parser.error("--max-seconds must be positive and not below --min-seconds")

    try:
        return run(args, ctx)
    except PlanningInputError as error:
        parser.error(str(error))
