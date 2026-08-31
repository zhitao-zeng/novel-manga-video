#!/usr/bin/env python3
"""Create three-frame contact sheets and a director-review JSON skeleton."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from novel_manga.production_models import ProductionPlan
from novel_manga.util import atomic_write_json, media_duration


def font(size: int):
    for path in (
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    episode_dir = args.episode_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(
        (episode_dir / "sd25_direct_plan.json").read_text(encoding="utf-8")
    )
    plan = ProductionPlan.model_validate_json(
        (episode_dir / "production_plan_sd25.json").read_text(encoding="utf-8")
    )
    groups = {group.group_id: group for group in plan.visual_groups}
    cards: list[Image.Image] = []
    review_rows = []
    for item in manifest["groups"]:
        group_id = item["group_id"]
        raw = episode_dir / groups[group_id].raw_video_path
        if not raw.is_file():
            raise FileNotFoundError(raw)
        seconds = media_duration(raw)
        frame_paths = []
        for index, ratio in enumerate((0.08, 0.50, 0.90), 1):
            frame = output_dir / "frames" / group_id / f"frame_{index}.jpeg"
            frame.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-ss",
                    f"{max(0.05, seconds * ratio):.3f}",
                    "-i",
                    str(raw),
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(frame),
                ],
                check=True,
            )
            frame_paths.append(frame)
        thumbs = [
            Image.open(path).convert("RGB").resize((270, 480))
            for path in frame_paths
        ]
        card = Image.new("RGB", (850, 590), "#111318")
        draw = ImageDraw.Draw(card)
        for index, thumb in enumerate(thumbs):
            card.paste(thumb, (10 + index * 280, 70))
        actions = " → ".join(str(value) for value in item["performance_contract"]["actions"])
        draw.text((12, 10), f"{group_id} / {item['shot_ids'][0]}", font=font(22), fill="white")
        draw.text((12, 548), actions[:64], font=font(18), fill="#ffd166")
        cards.append(card)
        review_rows.append(
            {
                "group_id": group_id,
                "planned_actions": item["performance_contract"]["actions"],
                "expected_visible_asset_ids": [
                    reference["asset_id"]
                    for reference in item["references"]
                    if reference["role"] == "character_identity_costume"
                ],
                "action_observed": None,
                "visible_asset_ids": [],
                "unexpected_named_characters": [],
                "unexpected_objects": [],
                "screen_direction_ok": None,
                "identity_consistency_score": None,
                "notes": "",
            }
        )
    for page_index in range(0, len(cards), 4):
        page_cards = cards[page_index : page_index + 4]
        sheet = Image.new("RGB", (1700, 1180), "#090a0d")
        for local_index, card in enumerate(page_cards):
            x = (local_index % 2) * 850
            y = (local_index // 2) * 590
            sheet.paste(card, (x, y))
        sheet.save(
            output_dir / f"review_sheet_{page_index // 4 + 1:02d}.jpeg",
            "JPEG",
            quality=92,
        )
    atomic_write_json(
        output_dir / "director_review_skeleton.json",
        {
            "schema_version": 1,
            "reviewer": "director-or-vlm",
            "groups": review_rows,
        },
    )
    print(json.dumps({"groups": len(cards), "pages": (len(cards) + 3) // 4}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
