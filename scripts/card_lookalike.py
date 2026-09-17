"""Check character-card distinguishability for a novel."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from novel_manga.application.assets.lookalike import main

if __name__ == '__main__':
    raise SystemExit(main())
