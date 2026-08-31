from __future__ import annotations

import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .config import Settings
from .ingest import SUPPORTED_SUFFIXES, read_novel
from .models import EpisodeStatus, SubmissionManifest, VideoRecord
from .pipeline import NovelPipeline
from .util import atomic_write_json


JobStatus = Literal["processing", "completed", "failed"]
PipelineRunner = Callable[[Path, str, str], SubmissionManifest]
DIRECT_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_novel_id(value: str | None) -> str:
    novel_id = (value or "").strip()
    if not novel_id:
        raise ValueError("缺少参数 novel_id")
    if len(novel_id) > 128:
        raise ValueError("novel_id 长度不能超过 128 个字符")
    if novel_id in {".", ".."} or any(
        not (character.isalnum() or character in "._-") for character in novel_id
    ):
        raise ValueError("novel_id 仅支持中文、字母、数字、点、下划线和连字符")
    return novel_id


def _safe_upload_name(filename: str | None) -> str:
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name:
        raise ValueError("上传文件缺少文件名")
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        supported = " / ".join(sorted(SUPPORTED_SUFFIXES))
        raise ValueError(f"不支持的文件类型 {suffix or '(无后缀)'}，支持: {supported}")
    return name


def _parse_positive_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} 必须是整数") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 0/1 或 true/false")


@dataclass(frozen=True)
class ApiConfig:
    output_root: Path = Path("/output")
    upload_root: Path = Path("/output/.uploads")
    state_root: Path = Path("/output/.jobs")
    provider: str = "mock"
    admission_mode: str | None = None
    bgm_path: Path | None = None
    job_workers: int = 1
    max_upload_bytes: int = 50 * 1024 * 1024
    require_local_models: bool = False
    model_supervisor_url: str = "http://127.0.0.1:18090"

    def __post_init__(self) -> None:
        if self.provider not in {"mock", "phanrouter", "command"}:
            raise ValueError("NOVEL_PROVIDER 必须是 mock、phanrouter 或 command")
        if self.admission_mode not in {None, "preview", "production"}:
            raise ValueError("NOVEL_ADMISSION_MODE 必须是 preview 或 production")
        if not 1 <= self.job_workers <= 2:
            raise ValueError("NOVEL_JOB_WORKERS 必须是 1 或 2")
        if self.max_upload_bytes < 1:
            raise ValueError("NOVEL_MAX_UPLOAD_BYTES 必须为正整数")
        if not self.model_supervisor_url.startswith(("http://", "https://")):
            raise ValueError("NOVEL_MODEL_SUPERVISOR_URL 必须是 HTTP(S) 地址")

    @classmethod
    def from_env(cls) -> "ApiConfig":
        output_root = Path(os.getenv("NOVEL_OUTPUT_ROOT", "/output"))
        admission_mode = os.getenv("NOVEL_ADMISSION_MODE")
        bgm_value = os.getenv("NOVEL_BGM_PATH")
        return cls(
            output_root=output_root,
            upload_root=Path(os.getenv("NOVEL_UPLOAD_ROOT", str(output_root / ".uploads"))),
            state_root=Path(os.getenv("NOVEL_JOB_ROOT", str(output_root / ".jobs"))),
            provider=os.getenv("NOVEL_PROVIDER", "mock"),
            admission_mode=admission_mode,
            bgm_path=Path(bgm_value) if bgm_value else None,
            job_workers=_parse_positive_int("NOVEL_JOB_WORKERS", 1, minimum=1, maximum=2),
            max_upload_bytes=_parse_positive_int(
                "NOVEL_MAX_UPLOAD_BYTES",
                50 * 1024 * 1024,
                minimum=1,
                maximum=2 * 1024 * 1024 * 1024,
            ),
            require_local_models=_env_flag("NOVEL_REQUIRE_LOCAL_MODELS", False),
            model_supervisor_url=os.getenv(
                "NOVEL_MODEL_SUPERVISOR_URL", "http://127.0.0.1:18090"
            ).rstrip("/"),
        )


class JobState(BaseModel):
    schema_version: Literal[1] = 1
    run_id: str
    novel_id: str
    original_filename: str
    novel_title: str
    upload_path: str
    status: JobStatus = "processing"
    finished: bool = False
    completed_count: int = Field(default=0, ge=0)
    total_expected: int = Field(ge=1)
    error: str | None = None
    created_at: str
    updated_at: str


class JobManager:
    """Durable single-process queue around the deterministic production controller."""

    def __init__(self, config: ApiConfig, runner: PipelineRunner | None = None):
        self.config = config
        self._runner = runner or self._run_pipeline
        self._lock = threading.RLock()
        self._executor: ThreadPoolExecutor | None = None
        self._futures: dict[str, Future[None]] = {}

    def start(self) -> None:
        with self._lock:
            self.config.output_root.mkdir(parents=True, exist_ok=True)
            self.config.upload_root.mkdir(parents=True, exist_ok=True)
            self.config.state_root.mkdir(parents=True, exist_ok=True)
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self.config.job_workers,
                    thread_name_prefix="novel-job",
                )
            for state in self._list_states_unlocked():
                if state.status != "processing":
                    continue
                if Path(state.upload_path).is_file():
                    self._submit_unlocked(state)
                    continue
                failed = state.model_copy(
                    update={
                        "status": "failed",
                        "finished": True,
                        "error": "服务恢复失败: 上传原文件不存在",
                        "updated_at": _now(),
                    }
                )
                self._write_state_unlocked(failed)

    def shutdown(self) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=False)

    @property
    def ready(self) -> bool:
        with self._lock:
            controller_ready = self._executor is not None and all(
                path.is_dir() and os.access(path, os.W_OK | os.X_OK)
                for path in (
                    self.config.output_root,
                    self.config.upload_root,
                    self.config.state_root,
                )
            )
        return controller_ready and bool(self.runtime_status()["ready"])

    def runtime_status(self) -> dict[str, object]:
        if not self.config.require_local_models:
            return {"required": False, "ready": True}
        url = f"{self.config.model_supervisor_url}/ready"
        try:
            request = urllib.request.Request(url, method="GET")
            with DIRECT_HTTP.open(request, timeout=2) as response:
                payload = json.loads(response.read())
        except (OSError, ValueError, urllib.error.URLError) as error:
            return {
                "required": True,
                "ready": False,
                "error": f"{type(error).__name__}: {error}"[:500],
            }
        all_models_ready = bool(payload.get("all_models_ready"))
        return {
            "required": True,
            "ready": bool(payload.get("ready")) and all_models_ready,
            "all_models_ready": all_models_ready,
            "active_stage": payload.get("active_stage"),
            "last_error": payload.get("last_error"),
            "models": payload.get("models", {}),
        }

    def _run_pipeline(self, source: Path, novel_id: str, title: str) -> SubmissionManifest:
        settings = Settings.from_env(
            provider=self.config.provider,
            output_root=self.config.output_root,
            bgm_path=self.config.bgm_path,
            admission_mode=self.config.admission_mode,
        )
        return NovelPipeline(settings).generate(source, novel_id=novel_id, title=title)

    def _state_path(self, novel_id: str) -> Path:
        digest = hashlib.sha256(novel_id.encode("utf-8")).hexdigest()
        return self.config.state_root / f"{digest}.json"

    def _read_state_unlocked(self, novel_id: str) -> JobState | None:
        path = self._state_path(novel_id)
        if not path.is_file():
            return None
        return JobState.model_validate_json(path.read_text(encoding="utf-8"))

    def _list_states_unlocked(self) -> list[JobState]:
        states: list[JobState] = []
        if not self.config.state_root.is_dir():
            return states
        for path in sorted(self.config.state_root.glob("*.json")):
            try:
                states.append(JobState.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return states

    def _write_state_unlocked(self, state: JobState) -> None:
        atomic_write_json(self._state_path(state.novel_id), state.model_dump(mode="json"))

    def get_state(self, novel_id: str) -> JobState | None:
        with self._lock:
            return self._read_state_unlocked(novel_id)

    def active_state(self, novel_id: str) -> JobState | None:
        state = self.get_state(novel_id)
        return state if state is not None and state.status == "processing" else None

    def upload_path(self, novel_id: str, filename: str) -> Path:
        suffix = Path(filename).suffix.lower()
        run_id = uuid.uuid4().hex
        digest = hashlib.sha256(novel_id.encode("utf-8")).hexdigest()
        directory = self.config.upload_root / digest
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{run_id}{suffix}"

    def enqueue(
        self,
        *,
        novel_id: str,
        original_filename: str,
        novel_title: str,
        upload_path: Path,
        total_expected: int,
    ) -> JobState:
        timestamp = _now()
        state = JobState(
            run_id=upload_path.stem,
            novel_id=novel_id,
            original_filename=original_filename,
            novel_title=novel_title,
            upload_path=str(upload_path.resolve()),
            total_expected=total_expected,
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self._lock:
            if self._executor is None:
                raise RuntimeError("任务服务尚未启动")
            current = self._read_state_unlocked(novel_id)
            if current is not None and current.status == "processing":
                raise RuntimeError(f"novel_id={novel_id} 已有生成任务在运行")
            self._archive_previous_manifest_unlocked(state)
            self._write_state_unlocked(state)
            self._submit_unlocked(state)
        return state

    def _submit_unlocked(self, state: JobState) -> None:
        if self._executor is None or state.novel_id in self._futures:
            return
        future = self._executor.submit(self._execute, state.novel_id, state.run_id)
        self._futures[state.novel_id] = future

        def forget(done: Future[None]) -> None:
            with self._lock:
                if self._futures.get(state.novel_id) is done:
                    self._futures.pop(state.novel_id, None)
                    current = self._read_state_unlocked(state.novel_id)
                    if (
                        current is not None
                        and current.status == "processing"
                        and current.run_id != state.run_id
                    ):
                        self._submit_unlocked(current)

        future.add_done_callback(forget)

    def _manifest_path(self, novel_id: str) -> Path:
        return self.config.output_root / novel_id / "manifest.json"

    def _archive_previous_manifest_unlocked(self, state: JobState) -> None:
        manifest = self._manifest_path(state.novel_id)
        if not manifest.is_file():
            return
        archive = manifest.with_name(f"manifest.previous-{state.run_id[:12]}.json")
        os.replace(manifest, archive)

    def _load_manifest(self, novel_id: str) -> SubmissionManifest | None:
        path = self._manifest_path(novel_id)
        if not path.is_file():
            return None
        return SubmissionManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def _safe_artifact(self, novel_id: str, relative: str) -> Path:
        novel_root = (self.config.output_root / novel_id).resolve()
        artifact = (novel_root / relative).resolve()
        if not artifact.is_relative_to(novel_root):
            raise ValueError("产物路径越界")
        return artifact

    def _validate_completed_manifest(
        self, state: JobState, manifest: SubmissionManifest
    ) -> list[str]:
        errors: list[str] = []
        if manifest.novel_id != state.novel_id:
            errors.append("manifest novel_id 不匹配")
        if manifest.video_count != state.total_expected:
            errors.append(
                f"manifest 视频数 {manifest.video_count} 与预期 {state.total_expected} 不一致"
            )
        expected_ids = [f"{state.novel_id}_{index}" for index in range(1, state.total_expected + 1)]
        actual_ids = [record.video_id for record in manifest.videos]
        if actual_ids != expected_ids:
            errors.append("video_id 未按从 1 开始的章节顺序连续编号")
        for record in manifest.videos:
            if record.status != EpisodeStatus.SUCCEEDED:
                errors.append(f"{record.video_id} 状态为 {record.status}")
                continue
            for label, relative in (
                ("视频", record.video_file),
                ("封面", record.video_cover),
                ("结束画面", record.ending_screen),
            ):
                try:
                    artifact = self._safe_artifact(state.novel_id, relative)
                except ValueError as error:
                    errors.append(f"{record.video_id} {label}: {error}")
                    continue
                if not artifact.is_file() or artifact.stat().st_size == 0:
                    errors.append(f"{record.video_id} {label}文件缺失或为空")
        return errors

    def _update_if_current(self, novel_id: str, run_id: str, **changes: object) -> None:
        with self._lock:
            state = self._read_state_unlocked(novel_id)
            if state is None or state.run_id != run_id:
                return
            updated = state.model_copy(update={**changes, "updated_at": _now()})
            self._write_state_unlocked(updated)

    def _execute(self, novel_id: str, run_id: str) -> None:
        state = self.get_state(novel_id)
        if state is None or state.run_id != run_id:
            return
        try:
            manifest = self._runner(
                Path(state.upload_path),
                state.novel_id,
                state.novel_title,
            )
            completed_count = sum(
                record.status == EpisodeStatus.SUCCEEDED for record in manifest.videos
            )
            errors = self._validate_completed_manifest(state, manifest)
            if errors:
                self._update_if_current(
                    novel_id,
                    run_id,
                    status="failed",
                    finished=True,
                    completed_count=completed_count,
                    error="; ".join(errors)[:4000],
                )
                return
            self._update_if_current(
                novel_id,
                run_id,
                status="completed",
                finished=True,
                completed_count=completed_count,
                error=None,
            )
        except Exception as error:
            self._update_if_current(
                novel_id,
                run_id,
                status="failed",
                finished=True,
                error=f"{type(error).__name__}: {error}"[:4000],
            )

    def refresh(self, novel_id: str) -> JobState | None:
        with self._lock:
            state = self._read_state_unlocked(novel_id)
            if state is None or state.status != "processing":
                return state
            try:
                manifest = self._load_manifest(novel_id)
            except (OSError, ValueError):
                return state
            if manifest is None:
                return state
            completed_count = sum(
                record.status == EpisodeStatus.SUCCEEDED for record in manifest.videos
            )
            if completed_count != state.completed_count:
                state = state.model_copy(
                    update={"completed_count": completed_count, "updated_at": _now()}
                )
                self._write_state_unlocked(state)
            return state

    def video_list(self, state: JobState) -> list[dict[str, str]]:
        if state.status != "completed":
            return []
        manifest = self._load_manifest(state.novel_id)
        if manifest is None:
            raise FileNotFoundError("生成清单不存在")
        novel = read_novel(
            state.upload_path,
            novel_id=state.novel_id,
            title=state.novel_title,
        )
        text_by_id = {
            f"{state.novel_id}_{episode.index}": episode.source_text
            for episode in novel.episodes
        }
        return [
            {
                "video_id": record.video_id,
                "video_title": record.video_title,
                "video_cover": f"{record.video_id}_cover.jpg",
                "ending_screen": f"{record.video_id}_ending.jpg",
                "text": text_by_id[record.video_id],
            }
            for record in manifest.videos
        ]

    def find_artifact(self, file_type: str, file_id: str) -> tuple[Path, str, str] | None:
        if len(file_id) > 256 or any(ord(character) < 32 for character in file_id):
            return None
        with self._lock:
            states = self._list_states_unlocked()
        for state in states:
            try:
                manifest = self._load_manifest(state.novel_id)
            except (OSError, ValueError):
                continue
            if manifest is None:
                continue
            for record in manifest.videos:
                if record.status != EpisodeStatus.SUCCEEDED:
                    continue
                if file_type == "video" and file_id == record.video_id:
                    try:
                        path = self._safe_artifact(state.novel_id, record.video_file)
                    except ValueError:
                        continue
                    return (path, "video/mp4", f"{record.video_id}.mp4")
                if file_type == "image" and file_id == f"{record.video_id}_cover":
                    try:
                        path = self._safe_artifact(state.novel_id, record.video_cover)
                    except ValueError:
                        continue
                    return (path, "image/jpeg", f"{record.video_id}_cover.jpg")
                if file_type == "image" and file_id == f"{record.video_id}_ending":
                    try:
                        path = self._safe_artifact(state.novel_id, record.ending_screen)
                    except ValueError:
                        continue
                    return (path, "image/jpeg", f"{record.video_id}_ending.jpg")
        return None


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "message": message},
    )


async def _save_upload(file: UploadFile, target: Path, limit: int) -> int:
    partial = target.with_suffix(target.suffix + ".partial")
    total = 0
    try:
        with partial.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise ValueError(f"上传文件超过大小限制 {limit} 字节")
                output.write(chunk)
        if total == 0:
            raise ValueError("上传文件为空")
        os.replace(partial, target)
        return total
    finally:
        partial.unlink(missing_ok=True)


def create_app(
    config: ApiConfig | None = None,
    *,
    manager: JobManager | None = None,
    runner: PipelineRunner | None = None,
) -> FastAPI:
    resolved_config = config or ApiConfig.from_env()
    job_manager = manager or JobManager(resolved_config, runner=runner)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        job_manager.start()
        try:
            yield
        finally:
            job_manager.shutdown()

    application = FastAPI(
        title="中文小说生成漫剧 SUT",
        version="0.13.0",
        lifespan=lifespan,
    )
    application.state.job_manager = job_manager
    application.state.api_config = resolved_config

    @application.get("/ready")
    async def ready():
        runtime = job_manager.runtime_status()
        if not job_manager.ready:
            if resolved_config.require_local_models:
                return JSONResponse(
                    status_code=503,
                    content={
                        "success": False,
                        "status": "not_ready",
                        "message": "本地模型尚未就绪",
                        "runtime": runtime,
                    },
                )
            return _error("服务尚未就绪", 503)
        if resolved_config.require_local_models:
            return {"success": True, "status": "ready", "runtime": runtime}
        return {"success": True, "status": "ready"}

    @application.post("/upload_novel")
    async def upload_novel(
        novel_id: str | None = Form(default=None),
        file: UploadFile | None = File(default=None),
    ):
        if file is None:
            return _error("缺少文件参数 file", 400)
        if not job_manager.ready:
            return _error("服务尚未就绪，暂不接受生成任务", 503)
        target: Path | None = None
        try:
            normalized_id = validate_novel_id(novel_id)
            existing = job_manager.active_state(normalized_id)
            if existing is not None:
                return {
                    "success": True,
                    "novel_id": normalized_id,
                    "message": f"视频生成任务正在运行，novel_id={normalized_id}",
                }
            original_filename = _safe_upload_name(file.filename)
            target = job_manager.upload_path(normalized_id, original_filename)
            await _save_upload(file, target, resolved_config.max_upload_bytes)
            novel_title = Path(original_filename).stem.strip() or normalized_id
            novel = read_novel(
                target,
                novel_id=normalized_id,
                title=novel_title,
            )
            job_manager.enqueue(
                novel_id=normalized_id,
                original_filename=original_filename,
                novel_title=novel.title,
                upload_path=target,
                total_expected=len(novel.episodes),
            )
            return {
                "success": True,
                "novel_id": normalized_id,
                "message": f"视频生成任务已启动，novel_id={normalized_id}",
            }
        except (OSError, ValueError, RuntimeError) as error:
            if target is not None:
                target.unlink(missing_ok=True)
            return _error(str(error), 400)
        finally:
            await file.close()

    @application.get("/generate_progress")
    async def generate_progress(novel_id: str | None = Query(default=None)):
        try:
            normalized_id = validate_novel_id(novel_id)
        except ValueError as error:
            return _error(str(error), 400)
        state = job_manager.refresh(normalized_id)
        if state is None:
            return _error(f"生成任务不存在: {normalized_id}", 404)
        try:
            videos = job_manager.video_list(state)
        except (OSError, KeyError, ValueError) as error:
            return _error(f"生成结果不可读取: {error}", 500)
        return {
            "success": True,
            "novel_id": normalized_id,
            "status": state.status,
            "finished": state.finished,
            "completed_count": state.completed_count,
            "total_expected": state.total_expected,
            "video_list": videos,
        }

    @application.get("/download/{file_type}/{file_id}")
    async def download(file_type: str, file_id: str):
        if file_type not in {"video", "image"}:
            return _error(f"不支持的文件类型: {file_type}", 400)
        artifact = job_manager.find_artifact(file_type, file_id)
        if artifact is None:
            label = "视频" if file_type == "video" else "图片"
            return _error(f"{label}文件不存在: {file_id}", 404)
        path, media_type, filename = artifact
        if not path.is_file() or path.stat().st_size == 0:
            label = "视频" if file_type == "video" else "图片"
            return _error(f"{label}文件不存在: {file_id}", 404)
        return FileResponse(path, media_type=media_type, filename=filename)

    return application


app = create_app()
