#!/usr/bin/env python3
"""Plan a chapter through the existing outline, screenplay and bounded repair flow."""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path
from novel_manga.planning.context import PlannerContext
from novel_manga.application.planning.requests import qwen_default
from novel_manga.application.planning.flow import run, PlanningInputError

def main(*, context: PlannerContext | None = None) -> int:
    ctx = context or PlannerContext.from_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--title")
    parser.add_argument("--episode-index", type=int, default=1)
    parser.add_argument("--bible", required=True, help="existing story_bible.json to reuse")
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
    parser.add_argument("--notes", default="", help="director feedback injected into this chapter's request")
    parser.add_argument("--grammar", type=Path, help="visual_grammar.json; defaults to <output-root>/<novel-id>/visual_grammar.json when present")
    parser.add_argument("--style", choices=("2d", "3d"), help="override profile.json style")
    parser.add_argument("--frame", choices=("9:16", "16:9"), help="override profile.json frame")
    parser.add_argument("--dry-run", action="store_true", help="build segments and request only")
    parser.add_argument("--replay", type=Path, help="validate an existing raw response instead of calling the model")
    parser.add_argument("--merge", type=int, default=1, help="chapters per episode: episode k covers chapters (k-1)*N+1..k*N")
    parser.add_argument("--tier", choices=("quality", "fast"), help="override profile.json tier")
    parser.add_argument("--outline-mode", choices=("coverage", "story"), default="coverage", help="first-pass planning; story is the experimental causal/scene outline")
    parser.add_argument("--outline-tokens", type=int, default=4096, help="explicit first-pass outline budget; one bounded retry, never forward incomplete output")
    parser.add_argument("--seed", type=int, help="optional model sampling seed for matched planning experiments")
    args = parser.parse_args()
    if args.outline_tokens <= 0:
        parser.error("--outline-tokens must be positive")

    try:
        return run(args, ctx)
    except PlanningInputError as error:
        parser.error(str(error))
