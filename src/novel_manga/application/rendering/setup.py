#!/usr/bin/env python3
from __future__ import annotations
from novel_manga.media.context import RenderContext
from novel_manga.media import generation
from novel_manga.application.configuration import project_root
from novel_manga.application.resolved_settings import resolve_book_settings

import json
import os
import re
import shlex
import sys
from pathlib import Path


from novel_manga.config import Settings
from novel_manga.models.bible import StoryBible
from novel_manga.media.adapters import FramedPhanRouter, FramedLocalH3
from novel_manga.media.asset_style import AssetStyle, image_backend
from novel_manga.media.context import RenderContext
from novel_manga.media import policy as media_policy
from novel_manga.media.postprocess import BatchRenderer
from novel_manga.media.policy import takes_past_cache
import novel_manga.application.rendering.moderation as moderation_repair
from dataclasses import replace as dc_replace

from novel_manga.application.profiles import plan_fingerprint, styled_bible

POLICY = "thin-media-v22-coverage-gate"
CHAT_CONTEXT_MESSAGES = 2   # earlier messages shown above the new ones on a chat card
CHAT_HISTORY_EPISODES = 3   # how far back to look for them


# ---- subtitle helpers (v16) ----


MAX_CER = 0.5          # kept in the report; no longer gates


# An English prompt (local H3) gets its retake note in English: H3 speaks what is written in Chinese, and
# read the note above out as dialogue - 雾月 58, 59, 97, 1262 and 1736 kept such takes.



from .requests import FEEDBACK_FILE

def lexicon_aliases() -> dict[str, str]:
    raw = os.getenv("NOVEL_ASR_PROTECTED_LEXICON_JSON", "{}")
    try:
        table = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    aliases: dict[str, str] = {}
    if isinstance(table, dict):
        for canonical, values in table.items():
            if isinstance(values, list):
                for alias in values:
                    aliases[str(alias)] = str(canonical)
    return aliases


def create_context(*, novel_dir: Path, episode_dir: Path, settings: Settings, bible: StoryBible, workers: int, max_attempts: int, profile: dict | None = None, inflight: int = 0, prescreen: bool = False, moderation_repair: bool = True, cache_only: bool = False, retake_failed: bool = False) -> RenderContext:
    ctx = RenderContext()
    ctx.options.cache_only = cache_only  # rebuild from clips already rendered; never generate
    ctx.novel_dir = novel_dir
    ctx.source_novel_dir = Path(os.environ.get('NOVEL_SOURCE_NOVEL_DIR') or novel_dir)
    ctx.options.inflight = inflight  # global cap on clips in flight across every runner of this novel (0 = none)
    ctx.options.prescreen = prescreen
    ctx.options.moderation_repair = moderation_repair
    ctx.episode_dir = episode_dir
    resolved = resolve_book_settings(novel_dir, supplied_profile=profile)
    ctx.profile = resolved.profile
    # Named frame_spec: ``self.frame`` is the frame-extraction method.
    ctx.frame_spec = resolved.frame
    # Canvas follows the frame; everything downstream (mux scale/crop, ASS
    # PlayRes, QC resolution) reads settings.width/height.
    ctx.settings = dc_replace(settings, width=ctx.frame_spec["width"], height=ctx.frame_spec["height"])
    ctx.bible = styled_bible(bible, ctx.profile, novel_dir) if (novel_dir / "profile.json").is_file() else bible
    effective = resolved.effective
    ctx.options.fast = effective["resolution"] == "480p"  # legacy alias for media/assets.py
    ctx.options.expressions = effective["expressions"]
    genre = resolved.genre
    ctx.asset_style = AssetStyle.for_genre(genre, frame_text=ctx.frame_spec["text"],
                                                    style=resolved.style,
                                                    backend=image_backend(ctx.settings))
    ctx.softening_rules = [*media_policy.SOFTEN, *((re.compile(pattern), replacement) for pattern, replacement in genre.get('soften', []))]
    ctx.options.voice_budget = generation.voice_budget_seconds()
    ctx.state.requested_workers = workers  # resolved after clip_plan is loaded (0 = one slot per clip)
    # Every tier gets a second attempt, but only when the first one failed
    # the speech gate (the retry loop runs on gate failures alone): a line
    # the model did not speak costs the line and its subtitles.
    ctx.options.max_attempts = max_attempts
    # On a free lane (local H3) a clip whose cached takes all failed gets fresh ones on a later run,
    # not the same verdict again (process_clip); a paid lane leaves further takes to a person.
    ctx.options.free_retries = takes_past_cache(ctx.settings, retake_failed, cache_only)
    resolution = effective["resolution"]
    local = ctx.settings.local_h3_base_url
    ctx.provider = (FramedLocalH3(ctx.settings, ctx.frame_spec, local, resolution=resolution)
                     if local else FramedPhanRouter(ctx.settings, ctx.frame_spec, resolution=resolution))
    from novel_manga.application.rendering import probe
    ctx.provider.before_video_submit = lambda: probe.check_before_submit(ctx)
    ctx.provider.prompt_aliases = {str(k): str(v) for k, v in (ctx.profile.get("prompt_aliases") or {}).items()}
    ctx.renderer = BatchRenderer(ctx.settings)
    ctx.work = episode_dir / "work"
    ctx.work.mkdir(parents=True, exist_ok=True)
    chat_screen_path = novel_dir / "chat_screen.json"
    ctx.chat_screen = json.loads(chat_screen_path.read_text(encoding="utf-8")) if chat_screen_path.is_file() else {}
    ctx.clip_plan = json.loads((episode_dir / "clip_plan.json").read_text(encoding="utf-8"))
    ctx.script = json.loads((episode_dir / "chapter_script.json").read_text(encoding="utf-8"))
    video_clips = [c for c in ctx.clip_plan["clips"] if c["kind"] == "video"]
    ctx.options.workers = ctx.state.requested_workers if ctx.state.requested_workers > 0 else max(1, len(video_clips))  # no second wave
    # Director corrections from the automatic episode review stay part of the
    # episode's state: they change the prompt (hence the cache key) of the
    # clips they name, so a corrected clip is regenerated exactly once.
    feedback_path = episode_dir / FEEDBACK_FILE
    ctx.feedback = json.loads(feedback_path.read_text(encoding="utf-8")) if feedback_path.is_file() else {}
    from novel_manga.application.repair.history import load as repair_history_load, generation_counts, generation_limit
    history_record=repair_history_load(episode_dir)
    counts=generation_counts(episode_dir, history_record)
    managed={cid for trial in history_record.get('trials',[]) if trial.get('managed')
             and trial['expected_plan']==plan_fingerprint(ctx.clip_plan)
             and trial['expected_notes']==ctx.feedback for cid in trial['clips']}
    from novel_manga.util import read_json
    grants = read_json(episode_dir / 'repair_budget_grants.json', {}) if managed else {}
    ctx.state.remaining={cid:max(0,generation_limit(episode_dir,cid,grants=grants)-counts.get(cid,0)) for cid in managed}
    technical_path = episode_dir / "technical_repair.json"
    ctx.state.black_checks = set(json.loads(technical_path.read_text()).get("black_clips", [])) if technical_path.is_file() else set()
    ctx.state.speech_checks = set(json.loads(technical_path.read_text()).get("speech_clips", [])) if technical_path.is_file() else set()
    asr_command = os.environ.get("NOVEL_ASR_COMMAND", "")
    ctx.asr_python = shlex.split(asr_command)[0] if asr_command else sys.executable
    ctx.asr_helper = (project_root() / 'scripts') / "thin_asr_segments.py"
    ctx.protected_terms = list(dict.fromkeys([
        *(c.name for c in bible.characters), *(l.split("：", 1)[0] for l in bible.locations),
        *(name for prop in bible.props for name in (prop.name, *prop.aliases))]))
    ctx.aliases = lexicon_aliases()
    alias_path = novel_dir / 'asr_aliases.json'
    if alias_path.is_file():
        ctx.aliases.update(json.loads(alias_path.read_text()))
    return ctx
