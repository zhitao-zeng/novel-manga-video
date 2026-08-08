#!/usr/bin/env python3
"""Build a dialogue-free episode cover around the locked series title artwork."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


WIDTH = 1080
HEIGHT = 1920


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", type=Path, required=True)
    parser.add_argument("--series-cover", type=Path, required=True)
    parser.add_argument("--episode-label", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--font",
        type=Path,
        default=Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    )
    return parser.parse_args()


def vertical_gradient(size: tuple[int, int], stops: list[tuple[float, int]]) -> Image.Image:
    width, height = size
    mask = Image.new("L", size)
    pixels = mask.load()
    for y in range(height):
        position = y / max(1, height - 1)
        left = stops[0]
        right = stops[-1]
        for index in range(len(stops) - 1):
            if stops[index][0] <= position <= stops[index + 1][0]:
                left, right = stops[index], stops[index + 1]
                break
        span = max(1e-6, right[0] - left[0])
        ratio = min(1.0, max(0.0, (position - left[0]) / span))
        value = round(left[1] + (right[1] - left[1]) * ratio)
        for x in range(width):
            pixels[x, y] = value
    return mask


def main() -> int:
    args = parse_args()
    if not args.font.is_file():
        raise FileNotFoundError(args.font)
    with Image.open(args.background).convert("RGB") as source:
        image = ImageOps.fit(source, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS)
    image = ImageEnhance.Contrast(image).enhance(1.08)
    image = ImageEnhance.Color(image).enhance(0.92)

    # Reserve the exact first-episode title treatment as the fixed series logo.
    with Image.open(args.series_cover).convert("RGB") as series:
        header = ImageOps.fit(
            series.crop((0, 0, series.width, min(series.height, 430))),
            (WIDTH, 430),
            method=Image.Resampling.LANCZOS,
        )
    header_mask = vertical_gradient(
        (WIDTH, 430),
        [(0.0, 255), (0.72, 255), (1.0, 0)],
    ).filter(ImageFilter.GaussianBlur(5))
    image.paste(header, (0, 0), header_mask)

    # Darken only the edges and title zone; keep the episode artwork readable.
    dark = Image.new("RGB", (WIDTH, HEIGHT), (3, 5, 12))
    overlay_mask = vertical_gradient(
        (WIDTH, HEIGHT),
        [(0.0, 45), (0.28, 0), (0.72, 0), (1.0, 120)],
    )
    image = Image.composite(dark, image, overlay_mask)

    draw = ImageDraw.Draw(image, "RGBA")
    font = ImageFont.truetype(str(args.font), 52)
    text_box = draw.textbbox((0, 0), args.episode_label, font=font, stroke_width=2)
    text_width = text_box[2] - text_box[0]
    center = WIDTH // 2
    y = 402
    draw.line((130, y + 31, center - text_width // 2 - 36, y + 31), fill=(225, 174, 82, 210), width=2)
    draw.line((center + text_width // 2 + 36, y + 31, 950, y + 31), fill=(225, 174, 82, 210), width=2)
    for x in (112, 968):
        draw.polygon(
            [(x, y + 31), (x + 10, y + 21), (x + 20, y + 31), (x + 10, y + 41)],
            fill=(158, 35, 42, 235),
            outline=(235, 191, 101, 255),
        )
    draw.text(
        (center - text_width / 2, y),
        args.episode_label,
        font=font,
        fill=(244, 207, 129, 255),
        stroke_width=2,
        stroke_fill=(35, 17, 14, 255),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(".partial.jpeg")
    image.save(partial, "JPEG", quality=96, subsampling=0)
    partial.replace(args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
