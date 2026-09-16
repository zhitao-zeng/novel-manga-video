"""One precise verdict per current take; original repair-review command."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from repair_review_flow_thin import review_batch
from verify_clips_thin import ROOT, parse_episodes

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episodes", required=True)
    parser.add_argument("--scope", choices=["candidates", "changed", "all", "flash"], required=True)
    parser.add_argument('--max-tokens', type=int)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--legacy-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    from novel_manga.util import load_dotenv
    load_dotenv(ROOT / ".env")
    result = review_batch(args.novel_dir.resolve(), sorted(parse_episodes(args.episodes)), args.scope,
                          args.state_dir.resolve(), args.legacy_dir.resolve(), args.workers, args.max_tokens)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["remaining"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
