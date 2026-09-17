#!/usr/bin/env python
"""Draw the variant cards phases.json names, one image each, through the renderer's own factory.

    build_phase_cards.py --novel-dir outputs/X [--only 沈玄川,阿曜] [--force] [--dry-run]

For every phase whose asset directory has no turnaround.jpeg: write spec.json (the base card's fields with
the phase's look laid over them, the prompt rebuilt the way build_selected() builds it) and draw the card
with ensure_card(), the same call the renderer makes for a base card.  Existing variant images are kept
unless --force, which moves them aside first.  The base card is never touched.  Prints one JSON line per
card, like build_cards_thin.py.
"""
from __future__ import annotations
from novel_manga.application.configuration import project_root

from novel_manga.media.asset_prompts import character_prompt
import argparse
import json
import os
import sys
import time
from dataclasses import replace as dc_replace
from pathlib import Path

from novel_manga.media.asset_style import CARD_STYLE_SUFFIX_3D
from novel_manga.media.asset_builder import FramedAssetFactory
from novel_manga.media.asset_policy import ModerationRejected
from novel_manga.media.asset_style import AssetStyle
from novel_manga.media.adapters import FramedPhanRouter
from novel_manga.media.common import log
from novel_manga.application.identity.phases import load_phases, phased
from novel_manga.application.profiles import frame_spec, load_genre, load_profile, styled_bible
from novel_manga.config import Settings  # noqa: E402
from novel_manga.models.bible import StoryBible

def load_dotenv(path: Path) -> None:
    """As thin_batch.py does.  build_cards_thin.py relies on the conductor's environment; a hand-run has none."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip().removeprefix("export ").strip(), value.strip().strip("'\""))


STYLE_MASTER_GUARD = (
    "【系列母版继承】参考图只锁定线稿粗细、二维平涂、赛璐璐阴影、色彩亮度、"
    "光影方向和整体动画制作规格；不得照抄参考图人物身份、脸型、发型、服装、姿势、"
    "场景结构或具体构图，必须严格按当前资产描述重新设计。"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--only", default="", help="comma-separated character names; default every character in phases.json")
    parser.add_argument("--force", action="store_true", help="redraw a variant that already has an image (the old one is moved aside)")
    parser.add_argument("--dry-run", action="store_true", help="write spec.json and print the prompt, draw nothing")
    parser.add_argument("--expressions", action="store_true", help="also draw expressions.jpeg for variants that have a turnaround (a lead's second view)")
    parser.add_argument("--style", choices=("2d", "3d"))
    parser.add_argument("--frame", choices=("9:16", "16:9"))
    parser.add_argument("--tier", choices=("quality", "fast"))
    args = parser.parse_args()

    load_dotenv(project_root() / ".env")
    novel_dir = args.novel_dir.resolve()
    phases = load_phases(novel_dir)
    if not phases:
        print(f"{novel_dir / 'series_assets' / 'phases.json'}: no phases", file=sys.stderr)
        return 2
    only = {name.strip() for name in args.only.split(",") if name.strip()}

    profile = load_profile(novel_dir, style=args.style, frame=args.frame, tier=args.tier)
    frame = frame_spec(profile)
    asset_style = AssetStyle.for_genre(load_genre(profile), frame_text=frame["text"])
    settings = Settings.from_env(provider="phanrouter", output_root=novel_dir.parent, admission_mode="preview")
    settings = dc_replace(settings, width=frame["width"], height=frame["height"])
    bible = StoryBible.model_validate_json((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    if (novel_dir / "profile.json").is_file():
        bible = styled_bible(bible, profile)
    provider = FramedPhanRouter(settings, frame)
    factory = FramedAssetFactory(settings, provider, style=asset_style)
    style_master = settings.style_master_path
    guard = STYLE_MASTER_GUARD if style_master is not None else ""
    index = {character.name: position for position, character in enumerate(bible.characters, start=1)}
    root = novel_dir / "series_assets"

    failures = 0
    for name, phase_list in phases.items():
        if only and name not in only:
            continue
        if name not in index:
            print(json.dumps({"name": name, "status": "error: not in the bible"}, ensure_ascii=False), flush=True)
            failures += 1
            continue
        character = bible.characters[index[name] - 1]
        base_id = f"character_{index[name]:03d}"
        for phase in phase_list:
            asset_id = str(phase.get("asset_id") or "")
            if not asset_id.startswith(base_id + "-"):
                print(json.dumps({"name": name, "asset_id": asset_id, "status": f"error: expected an id under {base_id}"}, ensure_ascii=False), flush=True)
                failures += 1
                continue
            directory = root / "characters" / asset_id
            output = directory / "turnaround.jpeg"
            if output.is_file() and not args.force:
                print(json.dumps({"name": name, "asset_id": asset_id, "status": "kept"}, ensure_ascii=False), flush=True)
                continue
            look = phased(character, phase)
            prompt = character_prompt(
                bible, look.name, look.appearance, look.base_costume or look.wardrobe,
                visual_archetype=look.visual_archetype, face_anchors=look.face_anchors, silhouette=look.silhouette,
                hair=look.hair, palette=look.palette, motion_signature=look.motion_signature,
            ) + guard
            if "3D" in bible.visual_style or "三维" in bible.visual_style:
                prompt += CARD_STYLE_SUFFIX_3D
            directory.mkdir(parents=True, exist_ok=True)
            spec = {
                "asset_id": asset_id, "base_asset": base_id, "phase": phase.get("label", ""),
                "from_chapter": phase.get("from"), "to_chapter": phase.get("to"),
                "name": look.name, "role": look.role, "gender": look.gender, "age": look.age,
                "appearance": look.appearance, "wardrobe": look.wardrobe, "visual_archetype": look.visual_archetype,
                "face_anchors": look.face_anchors, "silhouette": look.silhouette, "hair": look.hair, "palette": look.palette,
                "base_costume": look.base_costume, "signature_prop": look.signature_prop,
                "style_fingerprint": bible.style_fingerprint, "prompt": prompt,
            }
            (directory / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
            if args.dry_run:
                print(json.dumps({"name": name, "asset_id": asset_id, "status": "dry-run", "prompt_head": prompt[:160]}, ensure_ascii=False), flush=True)
                continue
            if output.is_file():
                stamp = time.strftime("%m%d-%H%M")
                for suffix in ("", ".request.json", ".task.json"):
                    old = directory / f"turnaround.jpeg{suffix}"
                    if old.is_file():
                        old.rename(directory / f"turnaround.superseded-{stamp}.jpeg{suffix}")
            started = time.monotonic()
            try:
                factory.ensure_card(prompt, output, reference=style_master)
                status = "built"
            except ModerationRejected as error:
                status = f"moderation: {str(error)[:120]}"
                failures += 1
            except Exception as error:  # noqa: BLE001 - one bad card must not stop the others
                status = f"error: {type(error).__name__}: {str(error)[:120]}"
                failures += 1
            print(json.dumps({"name": name, "asset_id": asset_id, "phase": phase.get("label", ""), "status": status,
                              "seconds": round(time.monotonic() - started, 1)}, ensure_ascii=False), flush=True)
    if args.expressions and not args.dry_run:
        # The second view build_selected() draws for a base card: the renderer references it for leads.
        for name, phase_list in phases.items():
            if (only and name not in only) or name not in index:
                continue
            character = bible.characters[index[name] - 1]
            for phase in phase_list:
                directory = root / "characters" / str(phase.get("asset_id") or "")
                primary, sheet = directory / "turnaround.jpeg", directory / "expressions.jpeg"
                if not primary.is_file() or sheet.is_file():
                    continue
                look = phased(character, phase)
                started = time.monotonic()
                try:
                    factory.ensure_card(factory._expression_prompt(bible, look.name, look.expression_profile), sheet, reference=primary)
                    status = "expressions built"
                except Exception as error:  # noqa: BLE001
                    status = f"expressions error: {type(error).__name__}: {str(error)[:120]}"
                    failures += 1
                print(json.dumps({"name": name, "asset_id": directory.name, "status": status,
                                  "seconds": round(time.monotonic() - started, 1)}, ensure_ascii=False), flush=True)
    return 2 if failures else 0
