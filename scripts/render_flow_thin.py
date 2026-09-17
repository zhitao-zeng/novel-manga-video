#!/usr/bin/env python3
"""Thin media runner: clip_plan.json -> assets -> Seedance 2.5 clips -> ASR -> episode.

  1. build series assets only up to the highest character/location the clips
     reference (asset ids follow StoryBible order, so ids stay stable)
  2. one Seedance request per clip: official-layout prompt + ordered reference images
  3. per clip: native audio, speech chunking (ffmpeg silencedetect), SenseVoice ASR
     per chunk, protected-name correction, hard gate (voice energy, CER <= 0.5),
     one regeneration on failure
  4. subtitles from ASR chunks, normalise to 1080x1920/25fps, crossfade join,
     ASS burn-in, loudnorm; cover and ending cards from real frames; media QC
Every remote task keeps its .task.json sidecar and every step skips work whose
output already exists, so a rerun resumes instead of paying again.
"""
from __future__ import annotations
from novel_manga.media import retries as clip_retries
import render_attempt_thin as clip_attempts
from novel_manga.media.issues import QualityIssue
import novel_manga.media.asset_inspection as asset_inspection
import novel_manga.media.asset_repair as asset_repair

import json
import copy
import os
import re
import shlex
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


from novel_manga.config import Settings
from novel_manga.models.bible import StoryBible
from novel_manga.story.dialogue import rewritten_dialogue
from novel_manga.media.common import log, reference_digests
from novel_manga.media.adapters import FramedPhanRouter, FramedLocalH3
from novel_manga.media.asset_builder import FramedAssetFactory
from novel_manga.media.asset_inspection import cards_sheet
from novel_manga.media.asset_records import load_privacy_ok, record_privacy_ok
from novel_manga.media.asset_repair import wait_for_inflight_redraws
from novel_manga.media import assets as media_assets
from novel_manga.media.asset_style import AssetStyle
from novel_manga.media.context import RenderContext, ClipResult, AssemblyResult
from novel_manga.media.resources import acquire_inflight_slot, release_inflight_slot
from novel_manga.media.policy import COMPLIANCE_SUFFIX, INPUT_TEXT_MARKER, PRIVACY_MARKER, MAX_ATTEMPTS_FREE, OUTPUT_MODERATION_MARKERS, PRESCREEN_RISK, RETRY_SUFFIX, RETRY_SUFFIX_H3, SUBMIT_BACKOFF_SECONDS
from novel_manga.media.subtitles import MIN_LINE_SIMILARITY
from novel_manga.media import policy as media_policy
from novel_manga.media import generation, cache, postprocess, subtitles
from novel_manga.media import analysis as media_analysis
from novel_manga.media.postprocess import BatchRenderer
from novel_manga.media.cache import CacheMiss
from novel_manga.media.policy import takes_past_cache, soften_prompt, resubmittable
from novel_manga.qc import inspect_media
from novel_manga.util import atomic_write_json, media_duration
import moderation_repair
from dataclasses import replace as dc_replace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from thin_profile import frame_spec, h3_prompt_fingerprint, is_fast, load_genre, load_profile, plan_fingerprint, styled_bible
from thin_profile import MAX_HOLD_SECONDS, media_qc_ignores

POLICY = "thin-media-v22-coverage-gate"
CHAT_CONTEXT_MESSAGES = 2   # earlier messages shown above the new ones on a chat card
CHAT_HISTORY_EPISODES = 3   # how far back to look for them











# ---- subtitle helpers (v16) ----
















MAX_CER = 0.5          # kept in the report; no longer gates








PLAN_WRITE_LOCK = threading.Lock()
# An English prompt (local H3) gets its retake note in English: H3 speaks what is written in Chinese, and
# read the note above out as dialogue - 雾月 58, 59, 97, 1262 and 1736 kept such takes.


FEEDBACK_FILE = "review_feedback.json"  # {clip_id: 导演修正}, written by the automatic episode review


















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













def prescreen_prompt(prompt: str) -> float:
    """Ask the local Qwen whether the prompt is likely to trip the video service's
    content filter (violence, gore, sexual content, gambling, drugs, politics)."""
    try:
        from novel_manga.llm.client import ask_json, obj
        verdict = ask_json([{"type": "text", "text": (
            "下面是一段给视频生成模型的提示词。判断它被平台内容审核拒绝的可能性（暴力打斗细节、血腥伤势、色情或挑逗台词、赌博、毒品、自残、敏感政治），"
            "risk 为 0 到 1，reason 一句话。只输出JSON。\n\n" + prompt[:6000])}], obj({"risk": {"type": "number"}, "reason": {"type": "string"}}), name="prescreen", max_tokens=120)
        return float(verdict.get("risk", 0.0))
    except Exception as error:  # noqa: BLE001 - the prescreen is advisory
        log(f"prescreen skipped ({type(error).__name__})")
        return 0.0











class ThinMediaRunner:
    def __init__(self, *, novel_dir: Path, episode_dir: Path, settings: Settings, bible: StoryBible, workers: int, max_attempts: int, profile: dict | None = None, inflight: int = 0, prescreen: bool = False, moderation_repair: bool = True, cache_only: bool = False, retake_failed: bool = False):
        self.context = RenderContext()
        self.context.cache_only = cache_only  # rebuild from clips already rendered; never generate
        self.context.novel_dir = novel_dir
        self.context.inflight = inflight  # global cap on clips in flight across every runner of this novel (0 = none)
        self.context.prescreen = prescreen
        self.context.moderation_repair = moderation_repair
        self.context.episode_dir = episode_dir
        self.context.profile = copy.deepcopy(profile) if profile else load_profile(novel_dir)
        # Named frame_spec: ``self.frame`` is the frame-extraction method.
        self.context.frame_spec = dict(frame_spec(self.context.profile))
        # Canvas follows the frame; everything downstream (mux scale/crop, ASS
        # PlayRes, QC resolution) reads settings.width/height.
        self.context.settings = dc_replace(settings, width=self.context.frame_spec["width"], height=self.context.frame_spec["height"])
        self.context.bible = styled_bible(bible, self.context.profile) if (novel_dir / "profile.json").is_file() else bible
        self.context.fast = is_fast(self.context.profile)
        genre = load_genre(self.context.profile)
        self.context.asset_style = AssetStyle.for_genre(genre, frame_text=self.context.frame_spec["text"])
        self.context.softening_rules = [*media_policy.SOFTEN, *((re.compile(pattern), replacement) for pattern, replacement in genre.get('soften', []))]
        self.context.voice_budget = generation.voice_budget_seconds()
        self.context._workers_arg = workers  # resolved after clip_plan is loaded (0 = one slot per clip)
        # Fast tier still gets a second attempt, but only when the first one
        # failed the speech gate (the retry loop runs on gate failures alone):
        # a line the model did not speak costs the line and its subtitles.
        self.context.max_attempts = 2 if self.context.fast else max_attempts
        # On a free lane (local H3) a clip whose cached takes all failed gets fresh ones on a later run,
        # not the same verdict again (process_clip); a paid lane leaves further takes to a person.
        self.context.free_retries = takes_past_cache(self.context.settings, retake_failed, cache_only)
        resolution = "480p" if self.context.fast else "720p"
        local = self.context.settings.local_h3_base_url
        self.context.provider = (FramedLocalH3(self.context.settings, self.context.frame_spec, local, resolution=resolution)
                         if local else FramedPhanRouter(self.context.settings, self.context.frame_spec, resolution=resolution))
        self.context.provider.prompt_aliases = {str(k): str(v) for k, v in (self.context.profile.get("prompt_aliases") or {}).items()}
        self.context.renderer = BatchRenderer(self.context.settings)
        self.context.work = episode_dir / "work"
        self.context.work.mkdir(parents=True, exist_ok=True)
        chat_screen_path = novel_dir / "chat_screen.json"
        self.context.chat_screen = json.loads(chat_screen_path.read_text(encoding="utf-8")) if chat_screen_path.is_file() else {}
        self.context.clip_plan = json.loads((episode_dir / "clip_plan.json").read_text(encoding="utf-8"))
        self.context.script = json.loads((episode_dir / "chapter_script.json").read_text(encoding="utf-8"))
        video_clips = [c for c in self.context.clip_plan["clips"] if c["kind"] == "video"]
        self.context.workers = self.context._workers_arg if self.context._workers_arg > 0 else max(1, len(video_clips))  # no second wave
        # Director corrections from the automatic episode review stay part of the
        # episode's state: they change the prompt (hence the cache key) of the
        # clips they name, so a corrected clip is regenerated exactly once.
        feedback_path = episode_dir / FEEDBACK_FILE
        self.context.feedback = json.loads(feedback_path.read_text(encoding="utf-8")) if feedback_path.is_file() else {}
        from repair_history import load as repair_history_load
        from managed_repair_thin import generated_counts, generation_limit
        history_record=repair_history_load(episode_dir)
        counts=generated_counts(history_record)
        managed={cid for trial in history_record.get('trials',[]) if trial.get('managed')
                 and trial['expected_plan']==plan_fingerprint(self.context.clip_plan)
                 and trial['expected_notes']==self.context.feedback for cid in trial['clips']}
        from novel_manga.util import read_json
        grants = read_json(episode_dir / 'repair_budget_grants.json', {}) if managed else {}
        self.context._managed_remaining={cid:max(0,generation_limit(episode_dir,cid,grants=grants)-counts.get(cid,0)) for cid in managed}
        technical_path = episode_dir / "technical_repair.json"
        self.context.black_checks = set(json.loads(technical_path.read_text()).get("black_clips", [])) if technical_path.is_file() else set()
        self.context.speech_checks = set(json.loads(technical_path.read_text()).get("speech_clips", [])) if technical_path.is_file() else set()
        asr_command = os.environ.get("NOVEL_ASR_COMMAND", "")
        self.context.asr_python = shlex.split(asr_command)[0] if asr_command else sys.executable
        self.context.asr_helper = Path(__file__).resolve().parent / "thin_asr_segments.py"
        self.context.protected_terms = list(dict.fromkeys([*(c.name for c in bible.characters), *(l.split("：", 1)[0] for l in bible.locations)]))
        self.context.aliases = lexicon_aliases()
        alias_path = novel_dir / 'asr_aliases.json'
        if alias_path.is_file():
            self.context.aliases.update(json.loads(alias_path.read_text()))

    # ---- assets ----
    def build_assets(self, clips=None):
        return media_assets.build_assets(self.context, clips)

    @staticmethod
    def purge_unreadable(root: Path, *, paths=None) -> list[Path]:
        return asset_inspection.purge_unreadable(root, paths=paths)

    def broken_assets(self, manifest, *, paths=None) -> list[Path]:
        return asset_inspection.broken_assets(self.context, manifest, paths=paths)

    def save_clip_plan(self) -> None:
        """Write the plan back without the runner's own bookkeeping keys."""
        with PLAN_WRITE_LOCK:
            plan = {**self.context.clip_plan, "clips": [{k: v for k, v in clip.items() if not k.startswith("_")} for clip in self.context.clip_plan["clips"]]}
            atomic_write_json(self.context.episode_dir / "clip_plan.json", plan)

    def repair_refused_prompt(self, clip: dict, attempt: int) -> bool:
        """Rewrite a prompt the text filter refuses, verified before it is paid for."""
        def assemble(base: str) -> str:
            saved = clip["prompt"]
            clip["prompt"] = base
            try:
                return self.clip_prompt(clip) + self.retry_suffix(clip, attempt) + (COMPLIANCE_SUFFIX if clip.get("_compliance") else "")
            finally:
                clip["prompt"] = saved

        # A guest character may be renamed to get past the filter (the card binding
        # is by image index, so the picture does not change), but the series leads
        # may not: their names run through every episode's plan and review.
        leads = tuple(c.name for c in self.context.bible.characters if str(getattr(c, "role", "")) in {"主角", "女主角", "男主角"})
        repaired = moderation_repair.repair(
            clip["prompt"], self.context.settings, assemble=assemble, protect=leads,
            log=lambda message: log(f"{clip['clip_id']}: {message}"))
        if not repaired:
            return False
        text, line_edits = repaired
        clip.setdefault("prompt_before_repair", clip["prompt"])
        clip["prompt"] = text
        # Subtitles, speech evaluation and H3 bindings must describe the same accepted words.
        clip.update(rewritten_dialogue(clip, line_edits))
        if line_edits:
            log(f"{clip['clip_id']}: repaired lines {[e['new'] for e in line_edits]}")
        # The plan is the cache key of a clip: keeping the accepted wording there
        # means a later run reuses this video instead of paying for it again.
        self.save_clip_plan()
        return True

    # ---- one clip ----
    def uses_h3_prompt(self, clip: dict) -> bool:
        return generation.uses_h3_prompt(self.context, clip)

    def clip_base(self, clip: dict) -> str:
        return generation.clip_base(self.context, clip)

    def retry_suffix(self, clip: dict, attempt: int) -> str:
        return generation.retry_suffix(self.context, clip, attempt)

    def english_correction_for_new_take(self, clip: dict) -> bool:
        """Keep old passed caches; a new corrected H3 take must use its English version."""
        note = str(self.context.feedback.get(clip['clip_id']) or '').strip()
        if not self.context.settings.local_h3_base_url or not note:
            return False
        from thin_profile import h3_prompt_outdated
        if h3_prompt_outdated(clip, note):
            raise RuntimeError('H3 correction needs a current English translation before generating a new take')
        if self.uses_h3_prompt(clip):
            return False
        clip.pop('prompt_h3_skip', None)
        self.save_clip_plan()
        return True

    @staticmethod
    def without_retry(prompt: str) -> str:
        return cache.without_retry(prompt)

    @staticmethod
    def references_match(saved: dict, references, digests: list[str]) -> bool:
        return cache.references_match(saved, references, digests)

    def request_matches(self, clip: dict, saved: dict, references, digests: list[str]) -> bool:
        return cache.request_matches(self.context, clip, saved, references, digests)

    def cached_take(self, clip: dict, attempt: int) -> bool:
        return cache.cached_take(self.context, clip, attempt)

    def approved_cached_take(self, clip: dict) -> dict | None:
        """Reuse proof from an existing take, never issue a new over-budget request."""
        from thin_profile import h3_prompt_outdated
        if self.context.settings.local_h3_base_url and h3_prompt_outdated(clip, str(self.context.feedback.get(clip["clip_id"], ""))):
            return None
        for path in sorted((self.context.work / "clips" / clip["clip_id"]).glob("attempt_*/asr.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                attempt = int(path.parent.name.split("_")[-1])
                if self.cached_take(clip, attempt):
                    record = self.recheck_speech(clip, record, path.with_name('clip.mp4'))
                    record = self.check_clip_black(clip, record, path.with_name("clip.mp4"))
                    if record["passed"]:
                        return {**record, "clip_id": clip["clip_id"], "video": str(path.with_name("clip.mp4"))}
            except (OSError, ValueError):
                continue
        return None

    def prescreens(self, clip: dict) -> bool:
        """Whether the clip's wording is scored for content-filter risk, and softened, before its first submission.
        Not an English (H3) prompt: the local model has no filter to get past, and the softening - Chinese
        substitutions and a Chinese compliance paragraph - rewrote its <d> lines and was read out as dialogue (雾月,
        2026-09-11/12: 329 clips in finals carried it, 30 of them audibly)."""
        return (self.context.prescreen and not self.context.cache_only and not self.uses_h3_prompt(clip)
                and not clip.get("_softened") and not clip.get("_prescreened"))

    def clip_prompt(self, clip: dict) -> str:
        return generation.clip_prompt(self.context, clip)

    def chosen_voices(self, clip: dict) -> tuple[list[tuple[int, Path]], list[str], float]:
        return generation.chosen_voices(self.context, clip)

    def reference_voices(self, clip: dict) -> tuple[Path, ...]:
        return generation.reference_voices(self.context, clip)

    def generate_clip(self, clip: dict, attempt: int) -> Path:
        if not self.context.cache_only:
            from clip_readiness import reference_issues
            reasons = getattr(self.context, "_blocked_clips", {}).get(clip["clip_id"], []) or reference_issues(clip, self.context.novel_dir)
            if reasons:
                if not hasattr(self.context, "_blocked_clips"):
                    self.context._blocked_clips = {}
                self.context._blocked_clips[clip["clip_id"]] = reasons
                raise RuntimeError("request blocked before generation: " + "; ".join(reasons))
        clip["_generated"] = False  # set once a new video is actually made: process_clip counts only those
        directory = self.context.work / "clips" / clip["clip_id"] / f"attempt_{attempt:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "clip.mp4"
        request, references, digests, retry = generation.build_request(self.context, clip, attempt)
        prompt = request['prompt']
        import repair_history
        cached = cache.current_video(self.context, clip, attempt, directory, references, digests, repair_history)
        if cached is not None:
            return cached
        if self.prescreens(clip):
            clip["_prescreened"] = True
            risk = prescreen_prompt(prompt)
            if risk >= PRESCREEN_RISK:
                clip["_softened"] = True
                log(f"{clip['clip_id']}: prescreen risk {risk:.2f}; softening the wording before the first submission")
                prompt = self.clip_prompt(clip) + retry
                request["prompt"] = prompt
        # Earlier runs (or the pre-v11 privacy retry) may hold the matching video
        # under another attempt directory; use it rather than paying again.
        cached = cache.other_video(self.context, clip, attempt, directory, references, digests)
        if cached is not None:
            return cached
        if self.context.cache_only:
            raise CacheMiss(f"{clip['clip_id']} attempt {attempt}: not in the cache")
        if self.english_correction_for_new_take(clip):
            return self.generate_clip(clip, attempt)
        if self.uses_h3_prompt(clip):
            from novel_manga.story.h3 import request_issues
            contradictions = request_issues(clip)
            if contradictions:
                if not hasattr(self.context, '_blocked_clips'):
                    self.context._blocked_clips={}
                self.context._blocked_clips[clip['clip_id']]=['request: '+issue for issue in contradictions]
                raise ValueError('H3 request needs identity repair: '+'; '.join(contradictions))
        remaining=getattr(self.context, '_managed_remaining',{}).get(clip['clip_id'])
        if remaining is not None and remaining<=0:
            raise ValueError('effective clip retry budget used; current cached take retained')
        wait_for_inflight_redraws(references)
        atomic_write_json(directory / "request.json", request)
        log(f"{clip['clip_id']} attempt {attempt}: requesting {clip['request_seconds']}s video with {len(references)} references")
        started = time.monotonic()
        slot = acquire_inflight_slot(self.context.novel_dir, self.context.inflight)
        # Everything before this point was waiting for a free slot of the key's
        # concurrency, not the video service working: timing them together hid
        # how long a generation really takes and how long the lane was queueing.
        submitted = time.monotonic()
        try:
          for wait in (*SUBMIT_BACKOFF_SECONDS, None):
            try:
                generation.submit(self.context, clip, request, output, references)
                break
            except RuntimeError as error:
                # Throttled at submission (higher --parallel): wait and resubmit
                # instead of failing the clip; content errors propagate at once.
                # An unconfirmed submission may already be a paid task: never resent from here.
                if wait is None or not resubmittable(error):
                    raise
                log(f"{clip['clip_id']} attempt {attempt}: video service throttled or H3 pool full ({str(error)[:80]}); retrying in {wait}s")
                time.sleep(wait)
        finally:
            release_inflight_slot(slot)
        clip["_generated"] = True
        if remaining is not None:
            self.context._managed_remaining[clip['clip_id']]-=1
        finished = time.monotonic()
        log(f"{clip['clip_id']} attempt {attempt}: video ready in {finished - submitted:.0f}s "
            f"(queued {submitted - started:.0f}s, {media_duration(output):.1f}s long)")
        return output

    def analyse_clip(self, clip: dict, video: Path) -> dict:
        from thin_profile import speech_gate_result
        raw = media_analysis.analyse_clip(self.context, clip, video)
        return speech_gate_result(self.context.novel_dir, self.check_clip_black(clip, self.recheck_speech(clip, raw, video), video), self.context.episode_dir)

    def recheck_speech(self, clip: dict, analysis: dict, video: Path) -> dict:
        return media_analysis.recheck_speech(self.context, clip, analysis, video)

    def check_clip_black(self, clip: dict, analysis: dict, video: Path) -> dict:
        """Targeted black-frame recovery must reject a black take before assembly."""
        if clip['clip_id'] not in getattr(self.context, 'black_checks', set()) or analysis.get('black_check_policy') == 3:
            return analysis
        from prepare_recovery_thin import black_ranges, brighten_dark_scene
        ranges = black_ranges(video, 0.2)
        # Use the same one-second threshold as final-media QC.
        issues = [issue for issue in analysis.get('issues') or [] if issue != QualityIssue.BLACK_FRAMES.code]
        exposure = False
        if ranges and any(end - start >= 1.0 for start, end in ranges):
            exposure = brighten_dark_scene(video, [(a,b) for a,b in ranges if b-a >= 1.0], self.context.episode_dir, clip['clip_id'])
            if exposure:
                ranges = []
                log(f"{clip['clip_id']}: restored detail in an underexposed scene; original archived, audio unchanged")
        if any(end - start >= 1.0 for start, end in ranges):
            issues.append(QualityIssue.BLACK_FRAMES.code)
        result = {**analysis, 'black_checked': True, 'black_check_policy': 3, 'black_ranges': ranges, 'issues': issues, 'passed': not issues,
                  **({'exposure_gamma': 1.6} if exposure else {})}
        atomic_write_json(video.parent / 'asr.json', result)
        return result

    def repair_rejected_reference(self, clip: dict, index: int) -> list[str]:
        return asset_repair.repair_rejected_reference(self.context, clip, index)

    def repair_privacy_cards(self, clip: dict) -> list[str]:
        return asset_repair.repair_privacy_cards(self.context, clip)

    def process_clip(self, clip: dict) -> ClipResult:
        """Generate, gate and regenerate one clip: once - or, on a free lane, twice per run past its cached takes.

        A privacy rejection is retried inside the same attempt: the cache key
        stays the one a later run looks for first, and the quality-retry
        suffix (about unclear speech) is not appended to a clip that never
        rendered.  Any other failure is reported per clip instead of taking
        the whole episode down; the clips in flight still finish and cache.
        """
        cached = getattr(self.context, "_approved_cached", {}).get(clip["clip_id"])
        if not self.context.cache_only and clip["clip_id"] in getattr(self.context, "_blocked_clips", {}):
            reasons = self.context._blocked_clips[clip["clip_id"]]
            log(f"{clip['clip_id']}: waiting for plan/assets: {'; '.join(reasons)}")
            return {"clip_id": clip["clip_id"], "attempts": [], "selected": None,
                    "error": "request blocked before generation", "blocked": reasons}
        attempts: list[dict] = []
        state = clip_retries.RetryState(attempt=1, limit=self.context.max_attempts)
        try:
            from repair_history import source_accepted_take
            accepted = source_accepted_take(self.context.work.parent, clip, str(self.context.feedback.get(clip['clip_id']) or ''))
            if accepted is not None:
                analysis = self.analyse_clip(clip, accepted)
                if analysis['passed']:
                    analysis = {**analysis, 'generated_this_run': False}
                    log(f"{clip['clip_id']}: existing video passed review against corrected source intent; reusing")
                    return {'clip_id':clip['clip_id'],'attempts':[analysis],'selected':analysis}
            if cached:
                # The previous prune may have removed native.wav. The normal
                # analysis path restores it from the video and reuses ASR, so
                # assembly has its audio without another generation or ASR call.
                cached = self.analyse_clip(clip, Path(cached["video"]))
                cached = {**cached, "generated_this_run": False}
                log(f"{clip['clip_id']}: reusing a matching passed take; no new request despite the old duration estimate")
                return {"clip_id": clip["clip_id"], "attempts": [cached], "selected": cached}
            while state.attempt <= state.limit:
                outcome = clip_attempts.generate_attempt(self, clip, state.attempt)
                if outcome.error is not None:
                    steps = clip_retries.recovery_steps(outcome.error, clip, state,
                                                       moderation_repair=self.context.moderation_repair)
                    retry = False
                    for step in steps:
                        if clip_attempts.apply_recovery(self, clip, state, step):
                            retry = True
                            break
                    if retry:
                        continue
                    raise outcome.error
                video = outcome.video
                analysis = self.analyse_clip(clip, video)
                analysis = {**analysis, "generated_this_run": bool(clip.get("_generated", False))}
                if not hasattr(self.context, "_ok_assets"):
                    self.context._ok_assets = set()
                self.context._ok_assets.update(ref["path"] for ref in clip.get("references", []))
                record_privacy_ok(self.context.novel_dir, (ref["path"] for ref in clip.get("references", []) if ref.get("role") != "voice"))
                attempts.append(analysis)
                log(f"{clip['clip_id']} attempt {state.attempt}: cer={analysis['cer']} peak={analysis['max_volume_db']} dB issues={analysis['issues']}")
                decision = clip_retries.after_analysis(state, analysis,
                    generated=bool(clip.get('_generated', True)), free_retries=self.context.free_retries)
                if decision.action == 'check_cache':
                    decision = clip_retries.after_analysis(state, analysis,
                        generated=bool(clip.get('_generated', True)), free_retries=self.context.free_retries,
                        next_cached=self.cached_take(clip, state.attempt + 1))
                state.attempt, state.limit = decision.attempt, decision.limit
                if decision.action == 'stop':
                    break
        except Exception as error:  # noqa: BLE001 - one clip must not sink the episode
            result = clip_retries.failure_result(clip['clip_id'], attempts, error)
            if 'retake_error' in result:
                log(f"{clip['clip_id']}: a further take failed ({result['retake_error'][:160]}); keeping the {len(attempts)} it has")
            else:
                log(f"{clip['clip_id']}: FAILED {result['error'][:200]}")
            return result
        selected = clip_retries.selected_take(attempts)
        return {"clip_id": clip["clip_id"], "attempts": attempts, "selected": selected}

    # ---- assembly ----
    def script_lines(self, clip_id: str) -> list[str]:
        return subtitles.script_lines(self.context, clip_id)

    @staticmethod
    def align_chunks(lines: list[str], chunks: list[dict], threshold: float = MIN_LINE_SIMILARITY) -> list[tuple[dict, str | None, float, list[tuple[int, str]]]]:
        return subtitles.align_chunks(lines, chunks, threshold)

    def subtitle_events(self, clip_id: str, analysis: dict) -> list[dict]:
        return subtitles.subtitle_events(self.context, clip_id, analysis)

    def _font(self, size: int):
        return postprocess._font(self.context, size)

    def landscape_cover(self, background: Path, output: Path, novel_title: str, art_title: str, label: str) -> Path:
        return postprocess.landscape_cover(self.context, background, output, novel_title, art_title, label)

    def landscape_card(self, background: Path, output: Path, novel_title: str, label: str, subtitle: str) -> Path:
        return postprocess.landscape_card(self.context, background, output, novel_title, label, subtitle)

    def frame(self, video: Path, second: float, output: Path) -> Path:
        return postprocess.frame(self.context, video, second, output)

    def chat_history(self, clip_id: str) -> dict[str, list[dict]]:
        return postprocess.chat_history(self.context, clip_id)

    def chat_segments(self, clip_id: str, clip_video: Path) -> list[dict]:
        return postprocess.chat_segments(self.context, clip_id, clip_video)

    def title_card_image(self, text: str, background: Path | None, output: Path) -> Path:
        return postprocess.title_card_image(self.context, text, background, output)

    def title_card_segment(self, clip: dict, background: Path | None) -> dict:
        return postprocess.title_card_segment(self.context, clip, background)

    def story_segments(self, results: list[dict]) -> list[dict]:
        return postprocess.story_segments(self.context, results)

    def assemble(self, results: list[dict]) -> AssemblyResult:
        from repair_delivery_thin import assembly_directory
        output_dir = assembly_directory(self.context.episode_dir)
        assembly = postprocess.assemble(self.context, results, output_dir)
        needs_subtitles = any(c.get('spoken_text') or c.get('lines') for c in self.context.clip_plan.get('clips', []))
        needs_subtitles |= any(t.get('text') and t.get('delivery_mode') in {'visible_dialogue', 'offscreen_dialogue', 'singing'}
                               for s in self.context.script.get('shots', []) for t in s.get('turns', []))
        qc = inspect_media(Path(assembly['final_video']), Path(assembly['cover']), Path(assembly['ending']),
                           Path(assembly['ass']), self.context.settings, output_dir / 'media_qc_report.json',
                           silent_outro_seconds=assembly['silent_outro_seconds'],
                           ignore_checks=tuple(media_qc_ignores(self.context.novel_dir,self.context.episode_dir)),
                           subtitles_required=needs_subtitles or bool(assembly['subtitle_events']))
        freeze = float(qc.get('checks', {}).get('long_freeze', {}).get('detail', {}).get('max_freeze_seconds', 0.0))
        other_checks = [v.get('passed') for k, v in qc.get('checks', {}).items() if k != 'long_freeze']
        return {**assembly, 'media_qc_passed': bool(qc.get('passed')), 'max_hold_seconds': freeze,
                'thin_passed': all(other_checks) and freeze <= MAX_HOLD_SECONDS, 'media_qc': qc}

    def run(self) -> dict:
        started = time.monotonic()
        clips = [clip for clip in self.context.clip_plan["clips"] if clip["kind"] == "video"]
        self.context._blocked_clips = {}
        self.context._approved_cached = {}
        if not self.context.cache_only:  # cached clips need no cards built (and none redrawn)
            from clip_readiness import plan_issues, reference_issues
            self.context._blocked_clips = plan_issues(self.context.clip_plan, self.context.script)
            for clip in clips:
                reasons = self.context._blocked_clips.get(clip["clip_id"])
                if reasons and all(r.startswith("duration:") for r in reasons):
                    cached = self.approved_cached_take(clip)
                    if cached:
                        self.context._approved_cached[clip["clip_id"]] = cached
                        self.context._blocked_clips.pop(clip["clip_id"])
            eligible = [clip for clip in clips if clip["clip_id"] not in self.context._blocked_clips and clip["clip_id"] not in self.context._approved_cached]
            if eligible:
                try:
                    self.build_assets(clips=eligible)
                except (RuntimeError, TimeoutError, OSError) as error:
                    # The card builder already made bounded attempts. One missing
                    # card must not prevent unrelated, fully prepared clips running.
                    log(f"assets: preparation incomplete ({type(error).__name__}); checking each clip's required images")
            for clip in eligible:
                missing = reference_issues(clip, self.context.novel_dir)
                if missing:
                    self.context._blocked_clips[clip["clip_id"]] = missing
        with ThreadPoolExecutor(max_workers=self.context.workers) as pool:
            results = list(pool.map(self.process_clip, clips))
        errored = [r["clip_id"] for r in results if r.get("error") or not r.get("selected")]
        failed = [r["clip_id"] for r in results if r.get("selected") and not r["selected"]["passed"]]
        if self.context.cache_only and errored:
            # Nothing is written: the episode keeps the report and final it had.
            log(f"cache-only: {len(errored)} clip(s) not in the cache ({', '.join(errored)}); episode left as it was")
            return {"status": "cache_miss", "elapsed_seconds": round(time.monotonic() - started, 1), "failed_clips": errored,
                    "gate_failed_clips": failed, "assembly": None, "clips": results}
        if not self.context.cache_only:
            from clip_readiness import save_check
            save_check(self.context.episode_dir, self.context.clip_plan, self.context._blocked_clips)
        report = {
            "policy": POLICY, "episode": self.context.episode_dir.name,
            # Batch drivers compare this with the current clip_plan.json to tell
            # a finished episode from one whose plan changed since.
            "clip_plan_fingerprint": plan_fingerprint(self.context.clip_plan),
            # ...and, on a local-H3 lane, of the English prompts it rendered from (thin_batch.render_status)
            "prompt_h3_fingerprint": h3_prompt_fingerprint(self.context.clip_plan) if self.context.settings.local_h3_base_url else None,
            "review_feedback": self.context.feedback,
            "clips": results, "failed_clips": errored, "gate_failed_clips": failed,
            "blocked_clips": self.context._blocked_clips,
        }
        if errored:
            log(f"{len(errored)} clip(s) have no video ({', '.join(errored)}); episode not assembled, re-run to retry only those")
            report.update({"status": "clips_failed", "assembly": None, "elapsed_seconds": round(time.monotonic() - started, 1)})
            atomic_write_json(self.context.episode_dir / "thin_media_report.json", report)
            from repair_history import record_render
            record_render(self.context.episode_dir, report)
            return report
        assembly = self.assemble(results)
        report.update({"status": "assembled", "assembly": assembly, "elapsed_seconds": round(time.monotonic() - started, 1)})
        atomic_write_json(self.context.episode_dir / "thin_media_report.json", report)
        from repair_history import record_render
        record_render(self.context.episode_dir, report)
        return report
