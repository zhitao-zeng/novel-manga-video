#!/usr/bin/env python3
"""Build a labelled contact sheet from canonical episode keyframes."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pattern", default="shot_???_turn_??.jpeg")
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--cell-width", type=int, default=270)
    parser.add_argument("--cell-height", type=int, default=480)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = sorted(args.input_dir.glob(args.pattern))
    if not paths:
        raise FileNotFoundError(f"no keyframes matched {args.input_dir / args.pattern}")
    if args.columns < 1:
        raise ValueError("--columns must be positive")
    rows = math.ceil(len(paths) / args.columns)
    sheet = Image.new("RGB", (args.columns * args.cell_width, rows * args.cell_height), "black")
    font = ImageFont.load_default()
    for index, path in enumerate(paths):
        with Image.open(path).convert("RGB") as source:
            cell = ImageOps.fit(
                source,
                (args.cell_width, args.cell_height),
                method=Image.Resampling.LANCZOS,
            )
        draw = ImageDraw.Draw(cell)
        relative = path.relative_to(args.input_dir)
        label_source = relative.with_suffix("").as_posix()
        label = label_source.replace("shot_", "S").replace("_turn_", " T")
        box = draw.textbbox((0, 0), label, font=font)
        draw.rectangle((5, 5, box[2] + 13, box[3] + 13), fill=(0, 0, 0))
        draw.text((9, 9), label, font=font, fill=(255, 255, 255))
        x = index % args.columns * args.cell_width
        y = index // args.columns * args.cell_height
        sheet.paste(cell, (x, y))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(".partial.jpeg")
    sheet.save(partial, "JPEG", quality=92, subsampling=0)
    partial.replace(args.output)
    print(f"wrote {len(paths)} frames to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
