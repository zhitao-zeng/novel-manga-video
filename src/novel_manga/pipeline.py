from __future__ import annotations

import json
import hashlib
import shutil
import time
from pathlib import Path

from .admission import (
    ADMISSION_POLICY_REVISION,
    admission_backend_identity,
)
from .config import Settings
from .ingest import read_novel
from .models import (
    EpisodePlanningBundle,
    EpisodePlan,
    EpisodeStatus,
    SeriesState,
    StoryBible,
    SubmissionManifest,
    VideoRecord,
)
from .production import SeriesAssetFactory
from .production_models import SeriesAssetManifest
from .production_runtime import EpisodeProductionRuntime
from .providers import build_providers
from .render import Renderer
from .runtime_backends import RuntimeEvidenceBackends
from .safety import scan_source
from .util import atomic_write_json


class NovelPipeline:
    """Executable production controller; it does not depend on Codex or a specific LLM."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.providers = build_providers(settings)
        self.renderer = Renderer(settings)
        self.asset_factory = SeriesAssetFactory(settings, self.providers.media)
        self.evidence = RuntimeEvidenceBackends(settings)
        self.episode_runtime = EpisodeProductionRuntime(
            settings,
            self.providers.media,
            self.renderer,
            self.asset_factory,
            self.evidence,
        )

    def _manifest_path(self, novel_dir: Path) -> Path:
        return novel_dir / "manifest.json"

    def _write_manifest(self, novel_dir: Path, manifest: SubmissionManifest) -> None:
        atomic_write_json(self._manifest_path(novel_dir), manifest.model_dump(mode="json"))

    @staticmethod
    def _digest(payload: object) -> str:
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _file_digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _planner_identity(self) -> dict:
        return {
            "planner_backend": self.settings.planner_backend,
            "planner_command_sha256": (
                hashlib.sha256(self.settings.planner_command.encode("utf-8")).hexdigest()
                if self.settings.planner_command
                else None
            ),
            "llm_base_url_sha256": (
                hashlib.sha256(self.settings.llm_base_url.encode("utf-8")).hexdigest()
                if self.settings.llm_base_url
                else None
            ),
            "llm_model": self.settings.llm_model,
            "planner_max_revisions": self.settings.planner_max_revisions,
            "planning_policy_revision": "novel-manga-plan-v3-script-quality",
        }

    @staticmethod
    def _archive_stale(path: Path, identity: str) -> None:
        if not path.exists():
            return
        target = path.with_name(f"{path.stem}.stale-{identity[:8]}{path.suffix}")
        if target.exists():
            target = path.with_name(f"{path.stem}.stale-{identity[:12]}{path.suffix}")
        shutil.move(path, target)

    def _load_or_build_bible(self, novel, novel_dir: Path) -> StoryBible:
        path = novel_dir / "story_bible.json"
        meta = path.with_suffix(path.suffix + ".request.json")
        identity_payload = {
            **self._planner_identity(),
            "novel_id": novel.novel_id,
            "novel_title": novel.title,
            "source_sha256": hashlib.sha256(novel.text.encode("utf-8")).hexdigest(),
        }
        identity = self._digest(identity_payload)
        if path.exists():
            bible = StoryBible.model_validate_json(path.read_text(encoding="utf-8"))
            if not meta.exists():
                atomic_write_json(meta, {**identity_payload, "request_sha256": identity, "artifact_sha256": self._file_digest(path), "origin": "preexisting-unversioned"})
                return bible
            saved = json.loads(meta.read_text(encoding="utf-8"))
            if saved.get("request_sha256") == identity:
                if saved.get("artifact_sha256") != self._file_digest(path):
                    atomic_write_json(meta, {**identity_payload, "request_sha256": identity, "artifact_sha256": self._file_digest(path), "origin": "manual-override"})
                return bible
            self._archive_stale(path, str(saved.get("request_sha256", "unknown")))
            self._archive_stale(meta, str(saved.get("request_sha256", "unknown")))
        self.providers.media.enter_stage("planner")
        bible = self.providers.planner.build_bible(novel)
        atomic_write_json(path, bible.model_dump(mode="json"))
        atomic_write_json(meta, {**identity_payload, "request_sha256": identity, "artifact_sha256": self._file_digest(path), "origin": "generated"})
        return bible

    def _load_or_build_plan(
        self,
        novel,
        episode,
        bible: StoryBible,
        episode_dir: Path,
        previous_state: SeriesState | None,
    ) -> EpisodePlanningBundle:
        path = episode_dir / "episode_plan.json"
        diagnosis_path = episode_dir / "chapter_diagnosis.json"
        quality_path = episode_dir / "script_quality_report.json"
        state_path = episode_dir / "updated_series_state.json"
        meta = path.with_suffix(path.suffix + ".request.json")
        identity_payload = {
            **self._planner_identity(),
            "episode_index": episode.index,
            "source_sha256": hashlib.sha256(episode.source_text.encode("utf-8")).hexdigest(),
            "style_fingerprint": bible.style_fingerprint,
            "previous_state_sha256": self._digest(
                previous_state.model_dump(mode="json") if previous_state else {}
            ),
        }
        identity = self._digest(identity_payload)
        companions = (diagnosis_path, quality_path, state_path)
        if path.exists() and all(item.exists() for item in companions):
            bundle = EpisodePlanningBundle(
                diagnosis=json.loads(diagnosis_path.read_text(encoding="utf-8")),
                plan=json.loads(path.read_text(encoding="utf-8")),
                quality_report=json.loads(quality_path.read_text(encoding="utf-8")),
                updated_series_state=json.loads(state_path.read_text(encoding="utf-8")),
            )
            if not meta.exists():
                raise ValueError(
                    f"audited episode plan is missing request provenance: {meta}"
                )
            saved = json.loads(meta.read_text(encoding="utf-8"))
            if saved.get("request_sha256") == identity:
                if not bundle.quality_report.passed:
                    raise ValueError("cached episode plan failed the script quality gate")
                return bundle
            self._archive_stale(path, str(saved.get("request_sha256", "unknown")))
            self._archive_stale(meta, str(saved.get("request_sha256", "unknown")))
            for companion in companions:
                self._archive_stale(companion, str(saved.get("request_sha256", "unknown")))
        elif path.exists() or any(item.exists() for item in companions):
            stale_identity = "incomplete-v3-bundle"
            for artifact in (path, meta, *companions):
                self._archive_stale(artifact, stale_identity)
        self.providers.media.enter_stage("planner")
        bundle = self.providers.planner.plan_episode_bundle(
            novel, episode, bible, previous_state
        )
        if not bundle.quality_report.passed:
            raise ValueError("script quality gate failed before media production")
        atomic_write_json(diagnosis_path, bundle.diagnosis.model_dump(mode="json"))
        atomic_write_json(path, bundle.plan.model_dump(mode="json"))
        atomic_write_json(quality_path, bundle.quality_report.model_dump(mode="json"))
        atomic_write_json(
            state_path, bundle.updated_series_state.model_dump(mode="json")
        )
        atomic_write_json(
            meta,
            {
                **identity_payload,
                "request_sha256": identity,
                "artifact_sha256": self._file_digest(path),
                "diagnosis_sha256": self._file_digest(diagnosis_path),
                "quality_report_sha256": self._file_digest(quality_path),
                "series_state_sha256": self._file_digest(state_path),
                "origin": "generated-v3-audited",
            },
        )
        return bundle

    def generate(self, source: str | Path, novel_id: str, title: str | None = None) -> SubmissionManifest:
        started = time.monotonic()
        novel = read_novel(source, novel_id=novel_id, title=title)
        root = self.settings.output_root.resolve()
        novel_dir = root / novel.novel_id
        novel_dir.mkdir(parents=True, exist_ok=True)

        bible = self._load_or_build_bible(novel, novel_dir)
        records = [
            VideoRecord(
                video_id=f"{novel.novel_id}_{episode.index}",
                video_title=episode.source_title,
                video_cover=f"{novel.novel_id}_{episode.index}/{novel.novel_id}_{episode.index}_cover.jpeg",
                ending_screen=f"{novel.novel_id}_{episode.index}/{novel.novel_id}_{episode.index}_ending.jpeg",
                video_file=f"{novel.novel_id}_{episode.index}/{novel.novel_id}_{episode.index}.mp4",
                text_count=episode.text_count,
                status=EpisodeStatus.PENDING,
            )
            for episode in novel.episodes
        ]
        manifest = SubmissionManifest(
            novel_id=novel.novel_id,
            novel_title=novel.title,
            video_count=len(records),
            videos=records,
        )
        self._write_manifest(novel_dir, manifest)

        # Plan every chapter while the planner owns the single GPU.  The old
        # per-episode loop repeatedly switched planner -> image -> audio ->
        # video -> planner and made the offline backend needlessly reload a
        # 27B model.  Hosted providers see the same ordering through a no-op
        # stage hook, so both deployment modes retain one controller path.
        previous_state: SeriesState | None = None
        planned_episodes: dict[int, EpisodePlan] = {}
        for episode, record in zip(novel.episodes, records, strict=True):
            episode_dir = novel_dir / record.video_id
            episode_dir.mkdir(parents=True, exist_ok=True)
            final_video = episode_dir / f"{record.video_id}.mp4"
            cover = episode_dir / f"{record.video_id}_cover.jpeg"
            ending = episode_dir / f"{record.video_id}_ending.jpeg"
            qc_path = episode_dir / "qc_report.json"

            if final_video.exists() and qc_path.exists():
                prior_qc = json.loads(qc_path.read_text(encoding="utf-8"))
                required_checks = {
                    "media_qc",
                    "subtitle_structure",
                    "subtitle_burn_in",
                    "speech_content",
                }
                state_path = episode_dir / "updated_series_state.json"
                if (
                    prior_qc.get("schema_version") == 2
                    and prior_qc.get("policy_revision") == ADMISSION_POLICY_REVISION
                    and prior_qc.get("passed")
                    and prior_qc.get("admission_mode") == self.settings.admission_mode
                    and prior_qc.get("provider") == self.settings.provider
                    and prior_qc.get("source_text_sha256")
                    == hashlib.sha256(episode.source_text.encode("utf-8")).hexdigest()
                    and prior_qc.get("style_fingerprint") == bible.style_fingerprint
                    and prior_qc.get("backend_identity") == admission_backend_identity(self.settings)
                    and required_checks <= set(prior_qc.get("checks", {}))
                    and state_path.is_file()
                ):
                    record.status = EpisodeStatus.SUCCEEDED
                    record.error = None
                    previous_state = SeriesState.model_validate_json(
                        state_path.read_text(encoding="utf-8")
                    )
                    plan_path = episode_dir / "episode_plan.json"
                    if plan_path.is_file():
                        cached_plan = EpisodePlan.model_validate_json(
                            plan_path.read_text(encoding="utf-8")
                        )
                        record.video_title = cached_plan.video_title
                    continue

            findings = scan_source(episode.source_text)
            if findings:
                record.status = EpisodeStatus.BLOCKED
                record.error = "safety gate: " + ", ".join(
                    sorted({finding.category for finding in findings})
                )
                atomic_write_json(
                    episode_dir / "safety_report.json", [finding.__dict__ for finding in findings]
                )
                self._write_manifest(novel_dir, manifest)
                continue

            record.status = EpisodeStatus.RUNNING
            self._write_manifest(novel_dir, manifest)
            try:
                planning = self._load_or_build_plan(
                    novel, episode, bible, episode_dir, previous_state
                )
                plan = planning.plan
                previous_state = planning.updated_series_state
                atomic_write_json(
                    novel_dir / "series_state.json",
                    previous_state.model_dump(mode="json"),
                )
                record.video_title = plan.video_title
                planned_episodes[episode.index] = plan
            except Exception as error:
                record.status = EpisodeStatus.FAILED
                record.error = f"{type(error).__name__}: {error}"[:1000]
            finally:
                self._write_manifest(novel_dir, manifest)

        series_assets: SeriesAssetManifest | None = None
        if planned_episodes:
            series_assets_path = novel_dir / "series_assets" / "manifest.json"
            if series_assets_path.exists():
                cached_assets = SeriesAssetManifest.model_validate_json(
                    series_assets_path.read_text(encoding="utf-8")
                )
                if cached_assets.style_fingerprint != bible.style_fingerprint:
                    self._archive_stale(
                        series_assets_path, cached_assets.style_fingerprint
                    )
            try:
                series_assets = self.asset_factory.build(
                    novel_dir / "series_assets", bible
                )
            except Exception as error:
                for episode, record in zip(novel.episodes, records, strict=True):
                    if episode.index in planned_episodes:
                        record.status = EpisodeStatus.FAILED
                        record.error = (
                            f"series asset generation failed: "
                            f"{type(error).__name__}: {error}"
                        )[:1000]
                self._write_manifest(novel_dir, manifest)
                planned_episodes.clear()

        # The media pass consumes only audited plans.  API and offline modes
        # differ solely in the provider adapter used by the shared runtime.
        for episode, record in zip(novel.episodes, records, strict=True):
            plan = planned_episodes.get(episode.index)
            if plan is None:
                continue
            if series_assets is None:
                raise RuntimeError("planned episodes exist without series assets")
            episode_dir = novel_dir / record.video_id
            final_video = episode_dir / f"{record.video_id}.mp4"
            cover = episode_dir / f"{record.video_id}_cover.jpeg"
            ending = episode_dir / f"{record.video_id}_ending.jpeg"
            record.status = EpisodeStatus.RUNNING
            record.error = None
            self._write_manifest(novel_dir, manifest)
            try:
                qc = self.episode_runtime.run(
                    novel_dir=novel_dir,
                    episode_dir=episode_dir,
                    episode=episode,
                    episode_plan=plan,
                    bible=bible,
                    series_assets=series_assets,
                    final_video=final_video,
                    cover=cover,
                    ending=ending,
                    video_id=record.video_id,
                    episode_count=len(novel.episodes),
                )
                if not qc["passed"]:
                    raise RuntimeError("production admission failed; see qc_report.json")
                record.status = EpisodeStatus.SUCCEEDED
                record.error = None
            except Exception as error:
                record.status = EpisodeStatus.FAILED
                record.error = f"{type(error).__name__}: {error}"[:1000]
            finally:
                self._write_manifest(novel_dir, manifest)

        elapsed = time.monotonic() - started
        description = {
            "format_version": "2.0",
            "runtime_contract": "novel-manga-production/v1",
            "codex_required": False,
            "novel_id": novel.novel_id,
            "novel_title": novel.title,
            "source_file": novel.source_path.name,
            "chaptered": novel.chaptered,
            "video_count": len(novel.episodes),
            "provider": self.settings.provider,
            "planner_backend": self.settings.planner_backend,
            "planner_max_revisions": self.settings.planner_max_revisions,
            "admission_mode": self.settings.admission_mode,
            "output_spec": {
                "width": 1080,
                "height": 1920,
                "fps": self.settings.fps,
                "video": "H.264/AAC MP4",
                "images": "JPEG",
            },
            "series_template": {
                "style_fingerprint": bible.style_fingerprint,
                "intro_seconds": self.settings.intro_seconds,
                "outro_seconds": self.settings.outro_seconds,
                "subtitle_safe_margin_bottom": 310,
            },
            "quality_backends": {
                "alignment": "external-command" if self.settings.align_command else "ffmpeg-silencedetect",
                "asr": "external-command" if self.settings.asr_command else "preview-mock",
                "face_consistency": "lightweight-reference-keyframe-proxy-v1",
            },
            "generation_seconds": round(elapsed, 3),
            "seconds_per_1000_source_chars": round(
                elapsed / max(1, sum(episode.text_count for episode in novel.episodes)) * 1000,
                3,
            ),
            "manifest": "manifest.json",
        }
        atomic_write_json(novel_dir / "说明文件.json", description)
        self._write_manifest(novel_dir, manifest)
        return manifest
