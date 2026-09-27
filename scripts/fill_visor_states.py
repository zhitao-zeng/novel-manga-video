"""Ask which stages show a wearer's faceplate shut (application/packing/visor.py); pack again afterwards."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from novel_manga.application.packing.visor import fill

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-dir", type=Path, required=True)
    print(json.dumps(fill(parser.parse_args().episode_dir), ensure_ascii=False, indent=1))
