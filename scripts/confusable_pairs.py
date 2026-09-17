"""Find recurring character confusion in saved review records."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from novel_manga.application.assets.confusable import main

if __name__ == '__main__':
    raise SystemExit(main())
