#!/usr/bin/env python
"""Report observable generation usage, local/paid lanes and recorded final duration.

Existing task and repair records are counted once; missing history and prices remain explicit.
"""
from __future__ import annotations
from novel_manga.application.configuration import project_root

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = project_root()
from novel_manga.reporting.usage import episode_rows, summarize
PRICING = REPO / "configs" / "pricing.json"


def load_rates() -> dict:
    if PRICING.is_file():
        return json.loads(PRICING.read_text(encoding="utf-8"))
    return {"currency": "", "video_per_second": {}, "image_per_card": 0.0}


def report(novel_dir: Path, chapters: set[int] | None, rates: dict, csv_path: Path | None) -> dict:
    result = summarize(novel_dir, rates, chapters)
    print(f"\n{novel_dir.name}: {result['episodes']} 集有记录，成片 {result['final_seconds'] / 60:.1f} 分钟")
    print(f"  可追溯生成素材 {result['video_seconds']:.0f} 秒；当前报告所选素材 {result['selected_seconds']:.0f} 秒")
    for group in result['groups']:
        label = '本地 H3' if group['provider'] == 'local_h3' else group['provider']
        print(f"  {label} / {group['model']} / {group['resolution']}: {group['attempts']} 次，{group['seconds']:.0f} 秒，"
              f"已记录 token {group['tokens']}（{group['missing_tokens']} 次缺 usage）")
    print(f"  图片 {result['images']} 次；图片范围为整本书"
          + (f"，共享自 {result['shared_assets']}" if result['shared_assets'] else ''))
    print(f"  有价格依据的付费接口估算：{result['known_api_cost']} {result['currency']}；"
          f"{result['unpriced_groups']} 组无法定价，本地算力未计价")
    print('  仅统计现存可追溯记录；缺失和已清理历史未反推，不等同于服务商账单。')
    print(f"  {result['coverage']['duration_from_request']} 次仅有请求时长，不能视为精确素材时长。")
    if csv_path:
        rows = [{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                 for k, v in r.items() if k != 'materials'} for r in episode_rows(novel_dir, chapters)]
        if rows:
            with csv_path.open('w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path)
    parser.add_argument("--all", action="store_true", help="every novel under outputs/ that has rendered episodes")
    parser.add_argument("--chapters", help="e.g. 11-70 or 11,12,13")
    parser.add_argument("--csv", type=Path, help="write the per-episode detail here")
    parser.add_argument("--json", action="store_true", help="print the totals as one JSON line")
    args = parser.parse_args()

    chapters = None
    if args.chapters:
        chapters = set()
        for piece in args.chapters.split(","):
            if "-" in piece:
                start, stop = piece.split("-")
                chapters.update(range(int(start), int(stop) + 1))
            elif piece.strip():
                chapters.add(int(piece))

    rates = load_rates()
    targets = []
    if args.all:
        root = REPO / "outputs"
        targets = [d for d in sorted(root.iterdir()) if d.is_dir() and (
            any(d.glob(f"{d.name}_*/clip_plan.json")) or any(d.glob(f"{d.name}_*/thin_media_report.json")))]
    elif args.novel_dir:
        targets = [args.novel_dir.resolve()]
    else:
        parser.error("pass --novel-dir or --all")

    totals = [report(directory, chapters, rates, args.csv if len(targets) == 1 else None) for directory in targets]
    totals = [row for row in totals if row]
    if len(totals) > 1:
        seconds = sum(row["video_seconds"] for row in totals)
        images = sum(row["images"] for row in totals)
        known = sum(row['known_api_cost'] for row in totals)
        unpriced = sum(row['unpriced_groups'] for row in totals)
        print(f"\n合计 {sum(row['episodes'] for row in totals)} 集有记录，素材 {seconds / 60:.1f} 分钟，图片 {images} 次；"
              f"已知接口估算 {known:.6f} {rates.get('currency', '')}，{unpriced} 组未定价")
    if args.json:
        print(json.dumps(totals, ensure_ascii=False))
    return 0
