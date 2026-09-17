"""One finite remaining audit queue shared by local Qwen and Flash workers."""
from __future__ import annotations
from novel_manga.application.configuration import project_root
import argparse
from pathlib import Path
import sys
ROOT = project_root()
from novel_manga.application.review.audit_flow import run

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--novel-dir', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--queue', type=Path, required=True)
    parser.add_argument('--lane', choices=['qwen', 'flash'], required=True)
    parser.add_argument('--workers', type=int, required=True)
    parser.add_argument('--sync-reviews',action='store_true',help='import primary audit results into episode reviews without rendering')
    parser.add_argument('--max-tokens',type=int)
    args = parser.parse_args()
    raise SystemExit(run(args.novel_dir.resolve(), args.state_dir.resolve(), args.queue.resolve(), args.lane, args.workers,
                         sync_reviews=args.sync_reviews,max_tokens=args.max_tokens))
