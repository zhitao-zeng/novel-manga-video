from __future__ import annotations

import hashlib
import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .admission import evaluate_episode_admission
from .config import Settings
from .face_consistency import evaluate_face_consistency
from .models import Episode, EpisodePlan, StoryBible
from .production import SeriesAssetFactory, compile_production_plan, sha256_file, sha256_text
from .production_models import ProductionPlan, RuntimeUnit, SeriesAssetManifest
from .providers.base import ImageResult, MediaProvider
from .qc import inspect_media
from .render import Renderer
from .runtime_backends import RuntimeEvidenceBackends, aggregate_asr
from .util import atomic_write_json, media_duration, run


def is_direct_reference_audio_visual_cache(payload: dict) -> bool:
    if any(str(key).startswith("postprocess") for key in payload):
        return False
    backend_identity = "".join(
        character
        for character in json.dumps(payload, ensure_ascii=False).casefold()
        if character.isalnum()
    )
    return "latentsync" not in backend_identity


class EpisodeProductionRuntime:
    def __init__(
        self,
        settings: Settings,
        media: MediaProvider,
        renderer: Renderer,
        assets: SeriesAssetFactory,
        evidence: RuntimeEvidenceBackends,
    ):
        self.settings = settings
        self.media = media
        self.renderer = renderer
        self.assets = assets
        self.evidence = evidence

    @staticmethod
    def _resolve(episode_dir: Path, path: str) -> Path:
        return episode_dir / path

    def _audio_identity(self, unit: RuntimeUnit) -> str:
        cache_source_sha256 = None
        cache_dir = os.environ.get("NOVEL_QWEN_TTS_CACHE_DIR")
        if cache_dir:
            cache_source = Path(cache_dir) / f"{unit.unit_id}.wav"
            if cache_source.is_file():
                cache_source_sha256 = sha256_file(cache_source)
        return sha256_text(
            json.dumps(
                {
                    "text": unit.text,
                    "voice": unit.voice,
                    "tts_model": self.settings.tts_model,
                    "tts_command": self.settings.tts_command,
                    "provider": self.settings.provider,
                    # A command string alone cannot identify a mutable local
                    # TTS cache.  Include the addressed WAV so speed/padding
                    # changes invalidate stale attempts and downstream video.
                    "tts_cache_source_sha256": cache_source_sha256,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    def _prepare_audio(self, episode_dir: Path, unit: RuntimeUnit) -> tuple[dict, dict]:
        output = self._resolve(episode_dir, unit.audio_path)
        meta = output.with_suffix(output.suffix + ".request.json")
        identity = self._audio_identity(unit)
        selected_path: Path | None = None
        selected_attempt = 0
        selected_asr: dict | None = None
        if output.is_file() and meta.is_file():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            if saved.get("request_sha256") == identity:
                selected_path = output
                selected_attempt = int(saved.get("attempt", 0))
                selected_asr = self.evidence.transcribe(unit.unit_id, unit.text, output)
                if not (
                    selected_asr.get("status") == "passed"
                    and float(selected_asr.get("cer", float("inf"))) <= self.settings.max_turn_cer
                ):
                    selected_path = None
        for attempt in range(1, self.settings.max_unit_attempts + 1):
            if selected_path is not None:
                break
            attempt_path = (
                episode_dir
                / "work"
                / "turn_audio_attempts"
                / unit.unit_id
                / identity[:8]
                / f"attempt_{attempt:02d}.wav"
            )
            if not attempt_path.is_file():
                instructions = (
                    f"标准普通话，语速自然偏快但清晰。逐字准确朗读：{unit.text}。"
                    f"人物和专有名词必须准确；角色语气：{unit.emotion}。"
                    + ("只做画外旁白。" if not unit.speaking else "保持角色音色稳定。")
                )
                self.media.synthesize(
                    unit.text,
                    attempt_path,
                    voice=unit.voice,
                    instructions=instructions,
                )
            row = self.evidence.transcribe(unit.unit_id, unit.text, attempt_path)
            selected_path, selected_attempt, selected_asr = attempt_path, attempt, row
            if row.get("status") == "passed" and float(row.get("cer", float("inf"))) <= self.settings.max_turn_cer:
                break
        assert selected_path is not None and selected_asr is not None
        output.parent.mkdir(parents=True, exist_ok=True)
        if selected_path.resolve() != output.resolve():
            shutil.copy2(selected_path, output)
        seconds = media_duration(output)
        if seconds > 13.5:
            selected_asr = {
                **selected_asr,
                "status": "failed",
                "cer": 999.0,
                "error": f"audio duration {seconds:.3f}s exceeds the 13.5s speaking-turn limit",
            }
        alignment = self.evidence.align(unit.unit_id, unit.text, output)
        unit.attempt = selected_attempt
        unit.audio_seconds = round(seconds, 6)
        unit.speech_start = round(float(alignment["speech_start"]), 6)
        unit.speech_end = round(float(alignment["speech_end"]), 6)
        unit.subtitle_alignment = str(alignment["evidence"])
        atomic_write_json(
            meta,
            {
                "request_sha256": identity,
                "attempt": selected_attempt,
                "audio_sha256": sha256_file(output),
                "voice": unit.voice,
                "text_sha256": sha256_text(unit.text),
            },
        )
        return selected_asr, alignment

    def _visual_identity(
        self,
        unit: RuntimeUnit,
        audio: Path,
        reference_board: Path,
    ) -> str:
        return sha256_text(
            json.dumps(
                {
                    "keyframe_prompt": unit.keyframe_prompt,
                    "motion_prompt": unit.motion_prompt,
                    "audio_sha256": sha256_file(audio),
                    "reference_board_sha256": sha256_file(reference_board),
                    "image_model": self.settings.image_model,
                    "video_model": self.settings.video_model,
                    "image_command_sha256": (
                        sha256_text(self.settings.image_command)
                        if self.settings.image_command
                        else None
                    ),
                    "video_command_sha256": (
                        sha256_text(self.settings.video_command)
                        if self.settings.video_command
                        else None
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    def _prepare_visual(
        self,
        episode_dir: Path,
        novel_dir: Path,
        unit: RuntimeUnit,
        series_assets: SeriesAssetManifest,
    ) -> dict:
        audio = self._resolve(episode_dir, unit.audio_path)
        canonical_video = self._resolve(episode_dir, unit.raw_video_path)
        canonical_keyframe = self._resolve(episode_dir, unit.keyframe_path)
        reference_board = self.assets.reference_board(
            episode_dir, unit, series_assets, novel_dir
        )
        identity = self._visual_identity(unit, audio, reference_board)
        meta = canonical_video.with_suffix(canonical_video.suffix + ".request.json")
        selected_video: Path | None = None
        selected_keyframe: Path | None = None
        selected_attempt = 0
        if canonical_video.is_file() and canonical_keyframe.is_file() and meta.is_file():
            saved = json.loads(meta.read_text(encoding="utf-8"))
            if (
                saved.get("request_sha256") == identity
                and is_direct_reference_audio_visual_cache(saved)
            ):
                selected_video = canonical_video
                selected_keyframe = canonical_keyframe
                selected_attempt = int(saved.get("attempt", 0))

        if selected_video is None:
            attempt = 1
            directory = (
                episode_dir
                / "work"
                / "visual_attempts"
                / unit.unit_id
                / identity[:8]
                / f"attempt_{attempt:02d}"
            )
            keyframe_path = directory / "keyframe.jpeg"
            video_path = directory / "clip.mp4"
            attempt_prompt = unit.keyframe_prompt + f" 本单元为独立构图第{attempt}次生成，禁止复用其他台词的完整构图。"
            keyframe = self.assets._ensure_image(
                attempt_prompt,
                keyframe_path,
                reference=reference_board,
            )
            if not video_path.is_file():
                self.media.create_video(
                    unit.motion_prompt,
                    keyframe,
                    video_path,
                    duration=min(14.0, max(4.0, float(unit.audio_seconds or 0.0) + 0.5)),
                    reference_audio=audio,
                )
            selected_video, selected_keyframe, selected_attempt = video_path, keyframe_path, attempt
        assert selected_video is not None and selected_keyframe is not None

        canonical_video.parent.mkdir(parents=True, exist_ok=True)
        canonical_keyframe.parent.mkdir(parents=True, exist_ok=True)
        if selected_video.resolve() != canonical_video.resolve():
            shutil.copy2(selected_video, canonical_video)
        if selected_keyframe.resolve() != canonical_keyframe.resolve():
            shutil.copy2(selected_keyframe, canonical_keyframe)
        unit.attempt = max(unit.attempt, selected_attempt)
        meta_payload = {
                "request_sha256": identity,
                "attempt": selected_attempt,
                "video_sha256": sha256_file(canonical_video),
                "audio_sha256": sha256_file(audio),
                "keyframe_sha256": sha256_file(canonical_keyframe),
                "keyframe_prompt": unit.keyframe_prompt,
                "motion_prompt": unit.motion_prompt,
                "reference_board": str(reference_board.relative_to(episode_dir)),
                "reference_board_sha256": sha256_file(reference_board),
                "workflow": "direct-reference-audio-video-no-lip-review-v1",
        }
        atomic_write_json(meta, meta_payload)
        return {
            "unit_id": unit.unit_id,
            "role": unit.role,
            "speaker_name": unit.speaker_name,
            "speaking": unit.speaking,
            "text": unit.text,
            "clip": unit.raw_video_path,
            "audio": unit.audio_path,
            "attempt": selected_attempt,
        }

    def _audit_delivered_asr(
        self,
        episode_dir: Path,
        final_video: Path,
        plan: ProductionPlan,
    ) -> dict:
        cursor = self.settings.intro_seconds
        rows = []
        delivered_dir = episode_dir / "work" / "delivered_turn_audio"
        for unit in plan.units:
            start = cursor + float(unit.speech_start or 0.0)
            end = cursor + float(unit.speech_end or unit.audio_seconds or 0.0)
            output = delivered_dir / f"{unit.unit_id}.wav"
            output.parent.mkdir(parents=True, exist_ok=True)
            run([
                "ffmpeg", "-y", "-v", "error", "-ss", f"{start:.6f}",
                "-i", str(final_video), "-t", f"{max(0.1, end - start):.6f}",
                "-vn", "-ar", "16000", "-ac", "1",
                "-c:a", "pcm_s16le", str(output),
            ])
            row = self.evidence.transcribe(unit.unit_id, unit.text, output)
            rows.append(
                {
                    **row,
                    "audio": str(output.relative_to(episode_dir)),
                    "delivered_start": round(start, 6),
                    "delivered_end": round(end, 6),
                }
            )
            cursor += float(unit.segment_seconds or 0.0)
        report = aggregate_asr(rows)
        report["audio_source"] = "delivered_final_video_per_turn_extract"
        return report

    def run(
        self,
        *,
        novel_dir: Path,
        episode_dir: Path,
        episode: Episode,
        episode_plan: EpisodePlan,
        bible: StoryBible,
        series_assets: SeriesAssetManifest,
        final_video: Path,
        cover: Path,
        ending: Path,
        video_id: str,
        episode_count: int,
    ) -> dict:
        plan = compile_production_plan(video_id, episode, episode_plan, bible, series_assets)
        plan_path = episode_dir / "production_plan.json"
        atomic_write_json(plan_path, plan.model_dump(mode="json"))

        asr_rows = []
        alignments = []
        for unit in plan.units:
            asr_row, alignment = self._prepare_audio(episode_dir, unit)
            asr_rows.append(asr_row)
            alignments.append(alignment)
        tts_asr_report = aggregate_asr(asr_rows)
        tts_asr_report["audio_source"] = "locked_tts_reference_before_video"
        atomic_write_json(episode_dir / "tts_asr_report.json", tts_asr_report)
        atomic_write_json(episode_dir / "alignment_report.json", {"units": alignments})
        atomic_write_json(plan_path, plan.model_dump(mode="json"))
        estimated_seconds = (
            self.settings.intro_seconds
            + self.settings.outro_seconds
            + sum(float(unit.audio_seconds or 0.0) + 0.3 for unit in plan.units)
        )
        if estimated_seconds > 300.5:
            raise ValueError(
                f"audio-bound episode estimate {estimated_seconds:.1f}s exceeds the five-minute limit"
            )

        visual_rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.settings.video_workers) as executor:
            futures = {
                executor.submit(
                    self._prepare_visual, episode_dir, novel_dir, unit, series_assets
                ): unit.unit_id
                for unit in plan.units
            }
            for future in as_completed(futures):
                visual_rows.append(future.result())
        visual_rows.sort(key=lambda row: str(row["unit_id"]))
        atomic_write_json(episode_dir / "visual_generation_report.json", {"units": visual_rows})
        face_consistency_report = evaluate_face_consistency(
            novel_dir=novel_dir,
            episode_dir=episode_dir,
            plan=plan,
            assets=series_assets,
        )
        atomic_write_json(episode_dir / "face_consistency_report.json", face_consistency_report)
        atomic_write_json(plan_path, plan.model_dump(mode="json"))

        turn_segments = []
        alignment_by_id = {row["unit_id"]: row for row in alignments}
        for unit in plan.units:
            segment, duration = self.renderer.mux_turn(
                self._resolve(episode_dir, unit.raw_video_path),
                self._resolve(episode_dir, unit.audio_path),
                self._resolve(episode_dir, unit.segment_path),
            )
            unit.segment_seconds = round(duration, 6)
            turn_segments.append(
                {
                    "unit_id": unit.unit_id,
                    "role": unit.role,
                    "segment": str(segment),
                    "duration": duration,
                    "alignment": alignment_by_id[unit.unit_id],
                }
            )
        story_seconds = sum(float(row["duration"]) for row in turn_segments)
        if self.settings.intro_seconds + story_seconds + self.settings.outro_seconds > 300.5:
            raise ValueError("planned episode exceeds the five-minute admission limit")

        first_keyframe = self._resolve(episode_dir, plan.units[0].keyframe_path)
        ending_background = novel_dir / series_assets.characters[0].primary_image
        self.renderer.make_card(
            first_keyframe,
            cover,
            bible.novel_title,
            f"第{episode.index:02d}集",
            episode_plan.video_title,
        )
        ending_copy = episode_plan.next_preview or "敬请期待后续"
        intro_card = episode_dir / "work" / "series_intro.jpeg"
        self.renderer.make_card(
            ending_background,
            intro_card,
            bible.novel_title,
            f"第{episode.index:02d}集",
            episode_plan.video_title,
        )
        self.renderer.make_card(
            ending_background,
            ending,
            bible.novel_title,
            "未完待续" if episode.index < episode_count else "本集完",
            ending_copy,
        )
        final_video, ass, joined, subtitle_events = self.renderer.assemble_production(
            intro_card, ending, turn_segments, final_video, episode_dir / "work"
        )
        asr_report = self._audit_delivered_asr(episode_dir, final_video, plan)
        atomic_write_json(episode_dir / "asr_report.json", asr_report)
        atomic_write_json(plan_path, plan.model_dump(mode="json"))

        trace = {
            "novel_id": video_id.rsplit("_", 1)[0],
            "video_id": video_id,
            "source_title": episode.source_title,
            "source_start": episode.source_start,
            "source_end": episode.source_end,
            "source_text_sha256": plan.source_text_sha256,
            "style_fingerprint": plan.style_fingerprint,
            "scenes": [scene.model_dump(mode="json") for scene in plan.scenes],
            "shots": [shot.model_dump(mode="json") for shot in plan.shots],
            "turns": [
                {
                    "unit_id": unit.unit_id,
                    "source_quote": unit.source_quote,
                    "source_quote_sha256": hashlib.sha256(unit.source_quote.encode("utf-8")).hexdigest(),
                    "text": unit.text,
                    "speaker_name": unit.speaker_name,
                    "speaking": unit.speaking,
                    "character_asset_ids": unit.character_asset_ids,
                    "location_asset_id": unit.location_asset_id,
                    "audio_path": unit.audio_path,
                    "keyframe_path": unit.keyframe_path,
                    "clip_path": unit.raw_video_path,
                    "subtitle_alignment": unit.subtitle_alignment,
                }
                for unit in plan.units
            ],
        }
        atomic_write_json(episode_dir / "content_trace.json", trace)
        media_qc = inspect_media(
            final_video,
            cover,
            ending,
            ass,
            self.settings,
            episode_dir / "media_qc_report.json",
        )
        admission = evaluate_episode_admission(
            settings=self.settings,
            plan=plan,
            media_qc=media_qc,
            ass=ass,
            clean_video=joined,
            delivered_video=final_video,
            subtitle_events=subtitle_events,
            asr_report=asr_report,
            face_consistency_report=face_consistency_report,
        )
        atomic_write_json(episode_dir / "admission_report.json", admission)
        atomic_write_json(episode_dir / "qc_report.json", admission)
        return admission
