"""Prepare the required episode assets and coordinate existing retries."""
from __future__ import annotations

import time
from ..providers.phanrouter_tasks import SubmissionUncertain
from .asset_builder import FramedAssetFactory, load_location_time
from .asset_inspection import purge_unreadable, broken_assets
from . import asset_repair
from .asset_policy import ModerationRejected, ASSET_BUILD_ROUNDS, ASSET_RETRY_SECONDS
from .common import log

def build_assets(ctx, clips=None):
    clips = ctx.clip_plan["clips"] if clips is None else clips
    character_ids = {ref["asset_id"] for clip in clips for ref in clip.get("references", []) if ref["role"] == "character"}
    location_ids = {ref["asset_id"] for clip in clips for ref in clip.get("references", []) if ref["role"] == "location"}
    prop_ids = {ref["asset_id"] for clip in clips for ref in clip.get("references", []) if ref["role"] == "prop"}
    required_images = {ctx.novel_dir / ref["path"] for clip in clips for ref in clip.get("references", [])
                       if ref.get("role") in {"character", "location"}}
    # Quality-mode construction may use/build the second view even if this
    # particular clip references only the primary. Fast production never does.
    if not ctx.fast:
        built_characters = {f"character_{i:03d}" for i, _ in enumerate(ctx.bible.characters, 1)}
        required_images.update(ctx.novel_dir / "series_assets" / "characters" / asset / name
                               for asset in character_ids & built_characters for name in ("turnaround.jpeg", "expressions.jpeg"))
    log(f"assets: {len(character_ids)} character cards + {len(location_ids)} locations")
    factory = FramedAssetFactory(ctx.settings, ctx.provider, style=ctx.asset_style,
                                 location_time=load_location_time(ctx.novel_dir))
    log(f"profile: style={ctx.profile['style']} frame={ctx.profile['frame']} canvas={ctx.settings.width}x{ctx.settings.height}")
    # Purge unreadable images BEFORE the factory runs.  A corrupt card is
    # not just a bad output: the factory feeds a character turnaround in as
    # the reference for its expression card, so one truncated download makes
    # every dependent request fail with an unrelated-looking error.
    purge_unreadable(ctx.novel_dir / "series_assets", paths=required_images)
    asset_repair.wait_for_inflight_redraws(sorted(required_images))
    manifest = None
    for attempt in range(1, ASSET_BUILD_ROUNDS + 1):
        try:
            manifest = factory.build_selected(ctx.novel_dir / "series_assets", ctx.bible, character_ids, location_ids,
                                              expressions=not ctx.fast, prop_ids=prop_ids)
        except ModerationRejected:
            raise
        except (RuntimeError, TimeoutError, OSError) as error:
            if isinstance(error, SubmissionUncertain):
                raise  # held until someone checks the bill: waiting out more rounds cannot release it
            # The hosted image service returns "图片生成失败，请稍后重试" during
            # its own incidents.  That is transient, so back off instead of
            # losing the whole chapter.
            if attempt == ASSET_BUILD_ROUNDS:
                raise
            log(f"assets: round {attempt} failed ({type(error).__name__}: {str(error)[:110]}); retrying in {ASSET_RETRY_SECONDS}s")
            time.sleep(ASSET_RETRY_SECONDS)
            continue
        broken = broken_assets(ctx, manifest, paths=required_images)
        if not broken:
            break
        # The hosted image CDN can serve an HTML notice or a truncated body;
        # `_download` stores those bytes verbatim, and reuse-existing-assets
        # would then lock the bad file in forever.  Drop it and regenerate.
        for path in broken:
            log(f"assets: {path.name} in {path.parent.name} is not a readable image, regenerating")
            for sidecar in (path, path.with_suffix(path.suffix + ".request.json"), path.with_suffix(path.suffix + ".task.json")):
                sidecar.unlink(missing_ok=True)
        if attempt == ASSET_BUILD_ROUNDS:
            raise RuntimeError(f"asset images still unreadable after {ASSET_BUILD_ROUNDS} rounds: {[str(p) for p in broken]}")
    for clip in clips:
        for ref in clip.get("references", []):
            path = ctx.novel_dir / ref["path"]
            if not path.is_file():
                raise RuntimeError(f"reference image missing after asset build: {path}")
    log("assets ready")
    return manifest
