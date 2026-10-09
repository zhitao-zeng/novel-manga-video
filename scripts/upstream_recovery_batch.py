#!/usr/bin/env python3
"""Resume one finite upstream recovery batch in its original episode workspaces."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from novel_manga.application.production.upstream_batch import main
if __name__=='__main__':raise SystemExit(main())
