"""Prepare reading/plan assets through the existing card builder."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from novel_manga.application.production.asset_preparation import main
if __name__=='__main__':
    raise SystemExit(main())
