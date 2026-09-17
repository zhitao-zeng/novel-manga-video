#!/usr/bin/env python
"""Contact sheets for the clips the second review flagged, so a person can check before paying.

The frames come from second_review.frames_of, not from a sampling of this script's own: when
they differed, I "verified" a verdict against frames the judge never saw and read the wrong
conclusion off it.  Whatever the judge looked at is what goes on the sheet.

    review_sheets.py <novel> [--votes 2] [--limit 40] [--out DIR]
"""
from novel_manga.application.configuration import project_root
import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = project_root()
import novel_manga.application.review.second_pass as sr

TILE_WIDTH = 420
BAR = 26


def sheet_for(clip_dir: Path, caption: str, out: Path) -> bool:
    video = clip_dir / "attempt_01" / "clip.mp4"
    if not video.is_file():
        return False
    frames = sr.frames_of(video)
    if not frames:
        return False
    try:
        tiles = []
        for path in frames:
            image = Image.open(path).convert("RGB")
            scale = TILE_WIDTH / image.width
            tiles.append(image.resize((TILE_WIDTH, round(image.height * scale))))
        width = sum(t.width for t in tiles)
        height = max(t.height for t in tiles)
        canvas = Image.new("RGB", (width, height + BAR), "black")
        x = 0
        for tile in tiles:
            canvas.paste(tile, (x, BAR))
            x += tile.width
        ImageDraw.Draw(canvas).text((8, 7), caption[:150], fill="white")
        canvas.save(out, quality=84)
        return True
    finally:
        for path in frames:
            path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("novel")
    parser.add_argument("--votes", type=int, default=2, help="至少几道判定都中")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    novel_dir = ROOT / "outputs" / args.novel
    final = novel_dir / "second_review_final.json"
    if not final.is_file():
        raise SystemExit(f"没有 {final}，先跑 second_review.py {args.novel}")
    rows = [r for r in json.loads(final.read_text(encoding="utf-8")) if r["votes"] >= args.votes]
    rows = rows[: args.limit]
    out_dir = args.out or (novel_dir / "review_sheets")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink()

    index, made = [], 0
    for n, row in enumerate(rows, 1):
        clip_dir = novel_dir / row["episode"] / "work" / "clips" / row["clip"]
        caption = f"{n:02d}  {row['episode']} {row['clip']}  {row['kind']} {row['score']}/100"
        name = out_dir / f"{n:02d}_{row['episode']}_{row['clip']}.jpg"
        if sheet_for(clip_dir, caption, name):
            made += 1
            index.append(f"{n:02d}  {row['episode']} {row['clip']}  {row['kind']}  看图 {row['score']}/100\n"
                         f"    判官看到：{row.get('saw','')[:100]}\n"
                         f"    读描述的理由：{row.get('why','')[:100]}")
    (out_dir / "index.txt").write_text("\n".join(index), encoding="utf-8")
    print(f"{args.novel}: {len(rows)} 段候选，出图 {made} 张 → {out_dir}")
    return 0
