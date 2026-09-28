"""Read pose/continuity into the episode ledger; reports problems without rendering or repacking."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from novel_manga.application.packing.posture import fill

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode-dir', type=Path, required=True)
    result = fill(parser.parse_args().episode_dir)
    print(json.dumps({'stages': len(result['stages']), 'issues': result['issues']}, ensure_ascii=False, indent=2))
    raise SystemExit(2 if result['issues'] else 0)
