#!/usr/bin/env python3
"""Resume a book's pre-render source audit, targeted rewrite, cards and H3 prompts.

One subprocess owns one unrendered episode. Existing footage is never rewritten
by this preparation queue. Reports are text checks, not video-review verdicts.
"""
from __future__ import annotations
from novel_manga.application.configuration import project_root

import argparse
from pathlib import Path
import sys
ROOT = project_root()
from novel_manga.application.preparation.flow import run

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--novel-dir', type=Path, required=True)
    parser.add_argument('--chapters', default='2001-3848')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--episode', type=int)
    args = parser.parse_args()
    return run(args)
