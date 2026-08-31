#!/usr/bin/env python3
"""Promote the user-approved 3D card library without replacing the 2D baseline."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path


ROOT = Path("outputs/ftj-anime-api10-v1/ftj-anime-api10/series_assets")
NOVEL_ROOT = ROOT.parent
TARGET_NOVEL = Path("outputs/ftj-anime-api10-v1/ftj-anime-api10-3d-script-ab")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    candidates = json.loads((ROOT / "3d_asset_manifest.json").read_text(encoding="utf-8"))
    approval_date = date.today().isoformat()
    for row in [*candidates["characters"], *candidates["locations"]]:
        row["status"] = "user_approved"
        row["approval_date"] = approval_date
        card_path = ROOT / Path(row["review_card"]).parent / "card.json"
        card = json.loads(card_path.read_text(encoding="utf-8"))
        card["status"] = "user_approved"
        card["approval_date"] = approval_date
        write_json(card_path, card)

    fingerprint_payload = {
        "style": candidates["style"],
        "style_master": candidates["style_master"],
        "cards": [
            (row["asset_id"], row["version"], row["review_card_sha256"])
            for row in [*candidates["characters"], *candidates["locations"]]
        ],
    }
    style_fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]

    baseline = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    character_candidate = {row["asset_id"]: row for row in candidates["characters"]}
    location_candidate = {row["asset_id"]: row for row in candidates["locations"]}
    promoted = {**baseline, "style_fingerprint": style_fingerprint}
    promoted["characters"] = []
    for row in baseline["characters"]:
        candidate = character_candidate[row["asset_id"]]
        promoted["characters"].append(
            {
                **row,
                "version": candidate["version"],
                "primary_image": "series_assets/" + candidate["views"]["portrait"],
                "secondary_image": "series_assets/"
                + candidate["views"]["front_fullbody"],
                "reference_scope": {
                    "inherit": [
                        "identity",
                        "hair",
                        "costume",
                        "3d_guoman_rendering",
                    ],
                    "exclude": [
                        "pose",
                        "composition",
                        "camera",
                        "background",
                        "lighting",
                    ],
                },
                "prompt_sha256": candidate["review_card_sha256"],
            }
        )
    promoted["locations"] = []
    for row in baseline["locations"]:
        candidate = location_candidate[row["asset_id"]]
        promoted["locations"].append(
            {
                **row,
                "version": candidate["version"],
                "primary_image": "series_assets/"
                + candidate["views"]["establishing_view"],
                "secondary_image": "series_assets/"
                + candidate["views"]["dialogue_angle_a"],
                "reference_scope": {
                    "inherit": [
                        "architecture",
                        "space",
                        "color",
                        "lighting",
                        "3d_guoman_rendering",
                    ],
                    "exclude": [
                        "composition",
                        "camera",
                        "temporary_people",
                        "text",
                    ],
                },
                "prompt_sha256": candidate["review_card_sha256"],
            }
        )
    write_json(ROOT / "manifest_3d.json", promoted)

    bible = json.loads((NOVEL_ROOT / "story_bible.json").read_text(encoding="utf-8"))
    bible["visual_style"] = (
        "高品质中国3D国漫连续剧风格：角色采用简化雕塑式面部结构、略大但非Q版的表现型眼睛、"
        "哑光无毛孔皮肤、束状设计发丝和稳定自然人体比例；服装、木石与金属采用克制的"
        "Toon-PBR材质，保持清晰轮廓、可读中间调和统一电影光照；禁止真人照片、二维线稿、"
        "塑料玩偶、欧美卡通和写实游戏截图。"
    )
    bible["palette"] = (
        "深蓝灰与炭黑作为结构基底，角色固有色保持清楚；暖白主光配克制金色边光，"
        "皮肤和布料不过曝，阴影保留层次。"
    )
    bible["style_fingerprint"] = style_fingerprint
    write_json(NOVEL_ROOT / "story_bible_3d.json", bible)

    target_assets = TARGET_NOVEL / "series_assets"
    target_assets.mkdir(parents=True, exist_ok=True)
    for name in ("characters", "locations"):
        link = target_assets / name
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            raise FileExistsError(
                f"refusing to replace non-symlink target asset directory: {link}"
            )
        link.symlink_to((ROOT / name).resolve(), target_is_directory=True)
    write_json(target_assets / "manifest.json", promoted)
    write_json(TARGET_NOVEL / "story_bible.json", bible)

    candidates["status"] = "user_approved"
    candidates["approval_date"] = approval_date
    candidates["style_fingerprint"] = style_fingerprint
    candidates["production_manifest_switched"] = True
    candidates["promoted_manifest"] = str((ROOT / "manifest_3d.json").resolve())
    candidates["target_novel_dir"] = str(TARGET_NOVEL.resolve())
    write_json(ROOT / "3d_asset_manifest.json", candidates)
    print(
        json.dumps(
            {
                "status": candidates["status"],
                "style_fingerprint": style_fingerprint,
                "promoted_manifest": str(ROOT / "manifest_3d.json"),
                "target_novel": str(TARGET_NOVEL),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
