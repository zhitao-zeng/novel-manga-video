#!/usr/bin/env python3
"""Finite recovery with existing native, review and Agent routes."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from novel_manga.application.production.upstream_batch import main
if __name__=='__main__':raise SystemExit(main(coverage=True))
