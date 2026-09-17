#!/usr/bin/env python3
"""Batch media command; generation, cache, analysis and postprocessing have shared owners."""
from __future__ import annotations
from novel_manga.application.configuration import project_root
import argparse
import json
import sys
from pathlib import Path
ROOT = project_root()
from novel_manga.config import Settings
from novel_manga.runtime_backends import normalize_text
from novel_manga.models.bible import StoryBible
from novel_manga.media.asset_inspection import cards_sheet
from novel_manga.application.profiles import load_profile
from novel_manga.application.rendering.flow import ThinMediaRunner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--novel-dir", type=Path, required=True)
    parser.add_argument("--episode", required=True, help="episode dir name under novel dir, e.g. fentian-thin-v4_1")
    parser.add_argument("--workers", type=int, default=4, help="clips submitted at once for this episode; 0 = one per clip")
    parser.add_argument("--inflight", type=int, default=0, help="global cap on clips in flight across all runners of the novel (lock-file semaphore); 0 = none")
    parser.add_argument("--prescreen", action="store_true", help="ask the local Qwen for content-filter risk and soften risky prompts before the first submission")
    parser.add_argument("--no-moderation-repair", dest="moderation_repair", action="store_false", default=True, help="do not bisect and rewrite a prompt the text filter keeps refusing")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--cache-only", action="store_true", help="rebuild the episode from clips already rendered; never generate, and write nothing when a clip is missing")
    parser.add_argument("--retake-failed", action="store_true", help="give clips whose cached takes all failed the speech gate fresh takes this run (always so on a local-H3 lane; on a paid one every take is paid for)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--assets-only", action="store_true", help="build the cards this episode needs, write series_assets/cards_sheet.jpg for review, and stop before any video")
    parser.add_argument("--style", choices=("2d", "3d"), help="override profile.json style")
    parser.add_argument("--frame", choices=("9:16", "16:9"), help="override profile.json frame")
    parser.add_argument("--tier", choices=("quality", "fast"), help="override profile.json tier")
    args = parser.parse_args()
    novel_dir = args.novel_dir.resolve()
    episode_dir = novel_dir / args.episode
    settings = Settings.from_env(provider="phanrouter", output_root=novel_dir.parent, admission_mode="preview")
    bible = StoryBible.model_validate_json((novel_dir / "story_bible.json").read_text(encoding="utf-8"))
    profile = load_profile(novel_dir, style=args.style, frame=args.frame, tier=args.tier)
    runner = ThinMediaRunner(novel_dir=novel_dir, episode_dir=episode_dir, settings=settings, bible=bible, workers=args.workers, max_attempts=args.max_attempts, profile=profile, inflight=args.inflight, prescreen=args.prescreen, moderation_repair=args.moderation_repair, cache_only=args.cache_only, retake_failed=args.retake_failed)
    clips = [c for c in runner.context.clip_plan["clips"] if c["kind"] == "video"]
    summary = {
        "profile": runner.context.profile, "canvas": f"{runner.context.settings.width}x{runner.context.settings.height}",
        "settings": {"provider": settings.provider, "image_model": settings.image_model, "video_model": settings.video_model, "admission_mode": settings.admission_mode, "poll_timeout": settings.poll_timeout, "outro_seconds": settings.outro_seconds, "font": str(settings.font_path)},
        "asr_python": runner.context.asr_python, "asr_helper": str(runner.context.asr_helper), "protected_terms": runner.context.protected_terms, "alias_count": len(runner.context.aliases),
        "clips": [{"clip_id": c["clip_id"], "seconds": c["request_seconds"], "references": [r["path"] for r in c["references"]], "spoken_chars": len(normalize_text(c.get("spoken_text", "")))} for c in clips],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return 0
    if args.assets_only:
        runner.build_assets()
        asset_ids = {ref["asset_id"] for clip in runner.context.clip_plan["clips"] for ref in clip.get("references", [])
                     if ref.get("role") in {"character", "location"}}
        sheet = cards_sheet(novel_dir, novel_dir / "series_assets" / "cards_sheet.jpg", asset_ids=asset_ids)
        print(json.dumps({"assets": "ready", "cards_sheet": str(sheet) if sheet else None}, ensure_ascii=False), flush=True)
        return 0
    report = runner.run()
    clip_rows = [{"clip_id": r["clip_id"], "attempts": len(r["attempts"]), "error": r.get("error"), **({"cer": r["selected"]["cer"], "peak_db": r["selected"]["max_volume_db"], "duration": r["selected"]["duration"]} if r.get("selected") else {})} for r in report["clips"]]
    assembly = {k: v for k, v in (report.get("assembly") or {}).items() if k != "media_qc"} or None
    print(json.dumps({"status": report["status"], "elapsed_seconds": report["elapsed_seconds"], "failed_clips": report["failed_clips"], "gate_failed_clips": report["gate_failed_clips"], "assembly": assembly, "clips": clip_rows}, ensure_ascii=False, indent=2), flush=True)
    if report["failed_clips"]:
        return 3
    return 0 if report["assembly"]["thin_passed"] and not report["gate_failed_clips"] else 2
