"""Write each clip a second prompt, in the shape MiniMax H3 was trained to read."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.rendering.h3 import main

if __name__ == "__main__":
    raise SystemExit(main())
