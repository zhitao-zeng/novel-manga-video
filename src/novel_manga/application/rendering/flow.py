#!/usr/bin/env python3
from __future__ import annotations
from . import requests, submission, clip_analysis, cached_takes
from .setup import create_context
from novel_manga.media import retries as clip_retries
import novel_manga.application.rendering.attempt as clip_attempts
import novel_manga.media.asset_repair as asset_repair

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


from novel_manga.config import Settings
from novel_manga.models.bible import StoryBible
from novel_manga.media.common import log
from novel_manga.media.asset_records import record_privacy_ok
from novel_manga.media import assets as media_assets
from novel_manga.media.results import ClipResult, AssemblyResult
from novel_manga.media import cache, postprocess, generation
from novel_manga.media.quality import inspect_media
from novel_manga.util import atomic_write_json
import novel_manga.application.rendering.moderation as moderation_repair

from novel_manga.application.profiles import h3_prompt_fingerprint, plan_fingerprint
from novel_manga.application.profiles import MAX_HOLD_SECONDS, media_qc_ignores

POLICY = "thin-media-v22-coverage-gate"
CHAT_CONTEXT_MESSAGES = 2   # earlier messages shown above the new ones on a chat card
CHAT_HISTORY_EPISODES = 3   # how far back to look for them


# ---- subtitle helpers (v16) ----


MAX_CER = 0.5          # kept in the report; no longer gates



# An English prompt (local H3) gets its retake note in English: H3 speaks what is written in Chinese, and
# read the note above out as dialogue - 雾月 58, 59, 97, 1262 and 1736 kept such takes.


FEEDBACK_FILE = "review_feedback.json"  # {clip_id: 导演修正}, written by the automatic episode review






class ThinMediaRunner:
    def __init__(self, *, novel_dir: Path, episode_dir: Path, settings: Settings, bible: StoryBible, workers: int, max_attempts: int, profile: dict | None = None, inflight: int = 0, prescreen: bool = False, moderation_repair: bool = True, cache_only: bool = False, retake_failed: bool = False):
        self.context = create_context(novel_dir=novel_dir, episode_dir=episode_dir, settings=settings,
            bible=bible, workers=workers, max_attempts=max_attempts, profile=profile,
            inflight=inflight, prescreen=prescreen, moderation_repair=moderation_repair,
            cache_only=cache_only, retake_failed=retake_failed)

    # ---- assets ----
    def build_assets(self, clips=None):
        return media_assets.build_assets(self.context, clips)

    def save_clip_plan(self) -> None:
        return requests.save_clip_plan(self.context)

    def repair_refused_prompt(self, clip: dict, attempt: int) -> bool:
        return requests.repair_refused_prompt(self.context, clip, attempt)

    # ---- one clip ----


    def retry_suffix(self, clip: dict, attempt: int) -> str:
        return requests.retry_suffix(self.context, clip, attempt)

    def english_correction_for_new_take(self, clip: dict) -> bool:
        return requests.english_correction_for_new_take(self.context, clip)


    def cached_take(self, clip: dict, attempt: int) -> bool:
        return cache.cached_take(self.context, clip, attempt)

    def correct_speech_request(self, clip: dict, analysis: dict) -> bool:
        return requests.correct_speech_request(self.context, clip, analysis)

    def approved_cached_take(self, clip: dict) -> dict | None:
        return cached_takes.approved_cached_take(self.context, clip)

    def prescreens(self, clip: dict) -> bool:
        return requests.prescreens(self.context, clip)

    def clip_prompt(self, clip: dict) -> str:
        return requests.clip_prompt(self.context, clip)


    def generate_clip(self, clip: dict, attempt: int) -> Path:
        return submission.generate_clip(self.context, clip, attempt)

    def analyse_clip(self, clip: dict, video: Path) -> dict:
        return clip_analysis.analyse_clip(self.context, clip, video)

    def recheck_speech(self, clip: dict, analysis: dict, video: Path) -> dict:
        return clip_analysis.recheck_speech(self.context, clip, analysis, video)

    def check_clip_black(self, clip: dict, analysis: dict, video: Path) -> dict:
        return clip_analysis.check_clip_black(self.context, clip, analysis, video)

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
        if self.context.options.first_pass_only:
            from novel_manga.application.preparation.store import clip_video
            existing = clip_video(self.context.episode_dir, clip['clip_id'])
            if existing is not None:
                # Keep the take address, without running inner-voice generation or
                # claiming that this deferred old take passes the current inputs.
                return {'clip_id': clip['clip_id'], 'attempts': [], 'review_deferred': True,
                        'selected': {'video': str(existing), 'passed': False}}
            selected = self.context.options.first_pass_clips
            if selected is not None and clip['clip_id'] not in selected:
                return {'clip_id': clip['clip_id'], 'attempts': [], 'preparation_deferred': True,
                        'selected': None}
        cached = self.context.state.approved_cached.get(clip["clip_id"])
        if not self.context.options.cache_only and clip["clip_id"] in self.context.state.blocked_clips:
            reasons = self.context.state.blocked_clips[clip["clip_id"]]
            log(f"{clip['clip_id']}: waiting for plan/assets: {'; '.join(reasons)}")
            return {"clip_id": clip["clip_id"], "attempts": [], "selected": None,
                    "error": "request blocked before generation", "blocked": reasons}
        attempts: list[dict] = []
        state = clip_retries.RetryState(attempt=1, limit=self.context.options.max_attempts)
        try:
            from novel_manga.story.voice_delivery import postmixed
            if postmixed(clip):
                from novel_manga.media.inner_voice import prepare
                prepare(self.context, clip)
            from novel_manga.application.repair.history import source_accepted_take
            accepted = source_accepted_take(self.context.work.parent, clip, str(self.context.feedback.get(clip['clip_id']) or ''))
            if accepted is not None and cache.sampling_matches(self.context, video=accepted):
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
                    steps = clip_retries.recovery_steps(outcome.error, self.context.state.clip(clip['clip_id']), state,
                                                       moderation_repair=self.context.options.moderation_repair)
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
                analysis = {**analysis, "generated_this_run": bool((self.context.state.clip(clip['clip_id']).generated if self.context.state.clip(clip['clip_id']).generated is not None else False))}
                if not hasattr(self.context.state, 'ok_assets'):
                    self.context.state.ok_assets = set()
                self.context.state.ok_assets.update(ref["path"] for ref in clip.get("references", []))
                record_privacy_ok(self.context.novel_dir, (ref["path"] for ref in clip.get("references", []) if ref.get("role") != "voice"))
                attempts.append(analysis)
                log(f"{clip['clip_id']} attempt {state.attempt}: cer={analysis['cer']} peak={analysis['max_volume_db']} dB issues={analysis['issues']}")
                from novel_manga.media.issues import is_speech_issue
                if (not analysis.get('passed') and any(is_speech_issue(code) for code in analysis.get('issues', [])) and
                        (getattr(self.context, 'profile', {}) or {}).get('repair_backend') == 'sandbox_agent'):
                    log(f"{clip['clip_id']}: deferring failed take to the repair agent; no inner quality retry")
                    break
                decision = clip_retries.after_analysis(state, analysis,
                    generated=bool((self.context.state.clip(clip['clip_id']).generated if self.context.state.clip(clip['clip_id']).generated is not None else True)), free_retries=self.context.options.free_retries,
                    local_h3=bool(self.context.settings.local_h3_base_url))
                if decision.action == 'check_cache':
                    decision = clip_retries.after_analysis(state, analysis,
                        generated=bool((self.context.state.clip(clip['clip_id']).generated if self.context.state.clip(clip['clip_id']).generated is not None else True)), free_retries=self.context.options.free_retries,
                        next_cached=self.cached_take(clip, state.attempt + 1),
                        local_h3=bool(self.context.settings.local_h3_base_url))
                if decision.action == 'correct_request' and not self.correct_speech_request(clip, analysis):
                    log(f"{clip['clip_id']}: speech correction produced no usable new request; keeping the failure for review")
                    break
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


    def assemble(self, results: list[dict]) -> AssemblyResult:
        from novel_manga.application.repair.delivery import assembly_directory
        from novel_manga.application.review.movie_context import assembly_inputs
        output_dir = assembly_directory(self.context.episode_dir)
        started = time.monotonic()
        assembly = postprocess.assemble(self.context, results, output_dir)
        encoded = time.monotonic()
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
        return {**assembly, 'source_inputs': assembly_inputs(self.context.clip_plan, {'clips': results}),
                'media_qc_passed': bool(qc.get('passed')), 'max_hold_seconds': freeze,
                'thin_passed': all(other_checks) and freeze <= MAX_HOLD_SECONDS, 'media_qc': qc,
                'timing': {'postprocess_s': round(encoded-started, 3),
                           'media_qc_s': round(time.monotonic()-encoded, 3)}}

    def run(self, *, assemble: bool = True) -> dict:
        started = time.monotonic()
        clips = [clip for clip in self.context.clip_plan["clips"] if clip["kind"] == "video"]
        self.context.state.blocked_clips = {}
        self.context.state.approved_cached = {}
        if not self.context.options.cache_only:  # cached clips need no cards built (and none redrawn)
            from novel_manga.application.preparation.readiness import plan_issues, reference_issues, render_risk_report, worn_reference_pairs
            from novel_manga.application.preparation.wearer_cards import check_references
            self.context.state.blocked_clips = plan_issues(self.context.clip_plan, self.context.script)
            for clip in clips:
                reasons = self.context.state.blocked_clips.get(clip["clip_id"])
                if reasons and all(r.startswith("duration:") for r in reasons):
                    cached = self.approved_cached_take(clip)
                    if cached:
                        self.context.state.approved_cached[clip["clip_id"]] = cached
                        self.context.state.blocked_clips.pop(clip["clip_id"])
            eligible = [clip for clip in clips if clip["clip_id"] not in self.context.state.blocked_clips and clip["clip_id"] not in self.context.state.approved_cached]
            if self.context.options.first_pass_only:
                from novel_manga.application.preparation.store import unfilmed_video_clips
                pending = unfilmed_video_clips(self.context.episode_dir, self.context.clip_plan)
                if self.context.options.first_pass_clips is not None:
                    pending &= self.context.options.first_pass_clips
                eligible = [clip for clip in eligible if clip['clip_id'] in pending]
            if eligible and not self.context.options.prepared_requests_only:
                try:
                    self.build_assets(clips=eligible)
                except (RuntimeError, TimeoutError, OSError) as error:
                    # The card builder already made bounded attempts. One missing
                    # card must not prevent unrelated, fully prepared clips running.
                    log(f"assets: preparation incomplete ({type(error).__name__}); checking each clip's required images")
            for clip in eligible:
                missing = reference_issues(clip, self.context.novel_dir)
                if missing:
                    self.context.state.blocked_clips[clip["clip_id"]] = missing
            ready = [clip for clip in eligible if clip['clip_id'] not in self.context.state.blocked_clips]
            # Inspect the final, available images once. Asset declarations are
            # inputs for selection, not proof that the current picture matches.
            if not self.context.options.prepared_requests_only:
                check_references(self.context.novel_dir, worn_reference_pairs({'clips': ready}, self.context.script))
            for cid, reasons in render_risk_report(self.context.episode_dir, self.context.clip_plan).items():
                if cid not in self.context.state.approved_cached:
                    self.context.state.blocked_clips.setdefault(cid, []).extend(reasons)
        with ThreadPoolExecutor(max_workers=self.context.options.workers) as pool:
            results = list(pool.map(self.process_clip, clips))
        errored = [r["clip_id"] for r in results if r.get("error") or not r.get("selected")]
        failed = [r["clip_id"] for r in results if r.get("selected") and not r["selected"]["passed"]]
        if self.context.options.cache_only and errored:
            # Nothing is written: the episode keeps the report and final it had.
            log(f"cache-only: {len(errored)} clip(s) not in the cache ({', '.join(errored)}); episode left as it was")
            return {"status": "cache_miss", "elapsed_seconds": round(time.monotonic() - started, 1), "failed_clips": errored,
                    "gate_failed_clips": failed, "assembly": None, "clips": results}
        if not self.context.options.cache_only:
            from novel_manga.application.preparation.readiness import save_check
            save_check(self.context.episode_dir, self.context.clip_plan, self.context.state.blocked_clips)
        report = {
            "policy": POLICY, "episode": self.context.episode_dir.name,
            # Batch drivers compare this with the current clip_plan.json to tell
            # a finished episode from one whose plan changed since.
            "clip_plan_fingerprint": plan_fingerprint(self.context.clip_plan),
            # ...and, on a local-H3 lane, of the English prompts it rendered from (thin_batch.render_status)
            "prompt_h3_fingerprint": h3_prompt_fingerprint(self.context.clip_plan) if generation.h3_lane(self.context) else None,
            "review_feedback": self.context.feedback,
            "clips": results, "failed_clips": errored, "gate_failed_clips": failed,
            "blocked_clips": self.context.state.blocked_clips,
        }
        if errored:
            log(f"{len(errored)} clip(s) have no video ({', '.join(errored)}); episode not assembled, re-run to retry only those")
        assembly = self.assemble(results) if assemble and not errored else None
        report.update({"status": "clips_failed" if errored else "assembled" if assemble else "clips_ready",
                       "assembly": assembly, "elapsed_seconds": round(time.monotonic() - started, 1)})
        atomic_write_json(self.context.episode_dir / "thin_media_report.json", report)
        from novel_manga.application.repair.history import record_render
        record_render(self.context.episode_dir, report)
        return report
