#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HOST = "127.0.0.1"
PORT = 18090
WORKER_PORT = 18100
MANIFEST_PATH = Path(os.getenv("NOVEL_MODEL_MANIFEST", "/app/runtime/model_manifest.json"))
LOG_ROOT = Path(os.getenv("NOVEL_RUNTIME_LOG_ROOT", "/output/.runtime"))
CUSTOM_STAGES = {
    "image-base",
    "image-edit",
    "audio",
    "audio-evidence",
    "video",
}
ALL_STAGES = {"planner", *CUSTOM_STAGES}
DIRECT_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _load_manifest() -> tuple[dict[str, Path], set[str]]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    models = {key: Path(value) for key, value in payload["models"].items()}
    optional = {str(name) for name in payload.get("optional_models", [])}
    unknown = optional - set(models)
    if unknown:
        raise ValueError(f"optional model names are absent from manifest: {sorted(unknown)}")
    return models, optional


def _checkpoint_complete(path: Path) -> tuple[bool, str | None]:
    if not path.is_dir():
        return False, "directory missing"
    if path.name.endswith(".partial"):
        return False, "partial directory"
    for pattern in ("*.incomplete", "*.aria2"):
        marker = next(path.rglob(pattern), None)
        if marker is not None:
            return False, f"incomplete download marker: {marker.name}"
    if not (path / "config.json").is_file() and not (path / "model_index.json").is_file():
        return False, "config.json/model_index.json missing"
    indexes = list(path.glob("*.safetensors.index.json")) + list(
        path.glob("**/*.safetensors.index.json")
    )
    for index in indexes:
        try:
            weights = json.loads(index.read_text(encoding="utf-8")).get("weight_map", {})
        except (OSError, ValueError):
            return False, f"invalid weight index: {index.name}"
        for filename in set(weights.values()):
            candidate = index.parent / filename
            if not candidate.is_file() or candidate.stat().st_size == 0:
                return False, f"missing indexed shard: {candidate.name}"
    weight_patterns = ("*.safetensors", "*.bin", "*.pth", "*.pt")
    has_weight = any(
        any(path.glob(pattern)) for pattern in weight_patterns
    ) or bool(indexes)
    if not has_weight:
        has_weight = any(
            any(path.glob(f"**/{pattern}")) for pattern in weight_patterns
        )
    return (True, None) if has_weight else (False, "model weights missing")


def _h3_checkpoint_complete(path: Path) -> tuple[bool, str | None]:
    required = {
        "diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
        "vae/minimax_h3_video_vae_fp16.safetensors",
        "vae/minimax_h3_audio_vae_fp32.safetensors",
    }
    if not path.is_dir():
        return False, "directory missing"
    missing = [name for name in sorted(required) if not (path / name).is_file()]
    if missing:
        return False, "missing H3 files: " + ", ".join(missing)
    empty = [name for name in sorted(required) if (path / name).stat().st_size == 0]
    return (False, "empty H3 files: " + ", ".join(empty)) if empty else (True, None)


class ModelSupervisor:
    def __init__(self) -> None:
        self.models, self.optional_models = _load_manifest()
        self.lock = threading.RLock()
        self.process: subprocess.Popen[bytes] | None = None
        self.log_stream = None
        self.active_stage: str | None = None
        self.last_error: str | None = None
        LOG_ROOT.mkdir(parents=True, exist_ok=True)

    def model_status(self) -> dict[str, dict[str, object]]:
        rows: dict[str, dict[str, object]] = {}
        for name, path in self.models.items():
            ready, error = (
                _h3_checkpoint_complete(path)
                if name == "video"
                else _checkpoint_complete(path)
            )
            rows[name] = {
                "path": str(path),
                "ready": ready,
                "optional": name in self.optional_models,
                "error": error,
            }
        return rows

    @property
    def all_models_ready(self) -> bool:
        return all(
            bool(item["ready"]) or bool(item["optional"])
            for item in self.model_status().values()
        )

    def _required_models(self, stage: str) -> list[str]:
        if stage == "planner":
            return ["planner"]
        if stage in {"image-base", "image-edit", "video"}:
            return [stage]
        if stage == "audio-evidence":
            return ["asr", "aligner"]
        if stage == "audio":
            backend = os.getenv("NOVEL_TTS_BACKEND", "voxcpm2").strip().casefold()
            if backend == "voxcpm2":
                return ["tts", "asr", "aligner"]
            if backend == "qwen":
                return ["tts-qwen", "asr", "aligner"]
            raise ValueError("NOVEL_TTS_BACKEND must be voxcpm2 or qwen")
        raise ValueError(f"unsupported model stage: {stage}")

    def _command(self, stage: str) -> list[str]:
        if stage == "planner":
            return [
                "/usr/local/bin/vllm",
                "serve",
                str(self.models["planner"]),
                "--host",
                HOST,
                "--port",
                str(WORKER_PORT),
                "--served-model-name",
                "qwen-planner",
                "--gpu-memory-utilization",
                os.getenv("NOVEL_PLANNER_GPU_MEMORY_UTILIZATION", "0.90"),
                "--max-model-len",
                os.getenv("NOVEL_PLANNER_MAX_MODEL_LEN", "32768"),
                "--enable-prefix-caching",
            ]
        if stage == "video":
            return [
                "/opt/venvs/h3/bin/python",
                "/opt/ComfyUI/main.py",
                "--listen",
                HOST,
                "--port",
                str(WORKER_PORT),
                "--base-directory",
                "/opt/ComfyUI",
                "--models-directory",
                str(self.models["video"]),
                "--input-directory",
                str(LOG_ROOT / "comfyui-input"),
                "--output-directory",
                str(LOG_ROOT / "comfyui-output"),
                "--temp-directory",
                str(LOG_ROOT / "comfyui-temp"),
                "--disable-auto-launch",
                "--disable-manager-ui",
                "--whitelist-custom-nodes",
                "novel_manga_h3_audio_drive",
                # The H3 diffusion model + Qwen3-VL encoder are ~52 GB.  An
                # A100-80G can keep them resident, while ComfyUI's default
                # async CPU offload exceeds the leaderboard's 32 GB RAM cap.
                "--gpu-only",
                "--disable-async-offload",
                "--cache-none",
            ]
        runtime = "image" if stage.startswith("image-") else "audio"
        return [
            f"/opt/venvs/{runtime}/bin/python",
            "/app/runtime/model_worker.py",
            "--stage",
            stage,
            "--host",
            HOST,
            "--port",
            str(WORKER_PORT),
            "--manifest",
            str(MANIFEST_PATH),
        ]

    def _health_url(self, stage: str) -> str:
        endpoint = "/health" if stage == "planner" else "/system_stats" if stage == "video" else "/ready"
        return f"http://{HOST}:{WORKER_PORT}{endpoint}"

    def _wait_ready(self, stage: str) -> None:
        timeout = float(os.getenv("NOVEL_MODEL_LOAD_TIMEOUT", "900"))
        deadline = time.monotonic() + timeout
        url = self._health_url(stage)
        while time.monotonic() < deadline:
            process = self.process
            if process is None or process.poll() is not None:
                code = None if process is None else process.returncode
                raise RuntimeError(f"{stage} worker exited during load (code={code})")
            try:
                with DIRECT_HTTP.open(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(1)
        raise TimeoutError(f"{stage} model did not become ready within {timeout:.0f}s")

    def stop(self) -> None:
        with self.lock:
            process = self.process
            self.process = None
            self.active_stage = None
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=30)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=10)
            if self.log_stream is not None:
                self.log_stream.close()
                self.log_stream = None

    def _gpu_used_mib(self) -> list[int] | None:
        """Return visible-GPU memory use, or None when nvidia-smi is unavailable."""
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        values: list[int] = []
        for line in completed.stdout.splitlines():
            value = line.strip()
            if value:
                values.append(int(value))
        return values

    def _wait_gpu_released(self) -> None:
        """Wait until the previous model's CUDA context has actually disappeared."""
        timeout = float(os.getenv("NOVEL_GPU_RELEASE_TIMEOUT", "120"))
        threshold = int(os.getenv("NOVEL_GPU_RELEASE_THRESHOLD_MIB", "256"))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            used = self._gpu_used_mib()
            if used is None or not used or max(used) <= threshold:
                return
            time.sleep(0.5)
        used = self._gpu_used_mib()
        raise TimeoutError(
            "previous model did not release GPU memory before stage switch "
            f"(used_mib={used}, threshold_mib={threshold}, timeout_s={timeout:.0f})"
        )

    def switch(self, stage: str) -> dict[str, object]:
        if stage not in ALL_STAGES:
            raise ValueError(f"unsupported model stage: {stage}")
        normalized = stage
        with self.lock:
            if (
                self.active_stage == normalized
                and self.process is not None
                and self.process.poll() is None
            ):
                return {"stage": normalized, "changed": False, "pid": self.process.pid}
            status = self.model_status()
            missing = [name for name in self._required_models(stage) if not status[name]["ready"]]
            if missing:
                details = "; ".join(f"{name}: {status[name]['error']}" for name in missing)
                raise RuntimeError(f"model stage {stage} is incomplete: {details}")
            self.stop()
            self._wait_gpu_released()
            log_path = LOG_ROOT / f"{normalized}.log"
            self.log_stream = log_path.open("ab", buffering=0)
            env = os.environ.copy()
            env.update(
                {
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "DIFFUSERS_OFFLINE": "1",
                    "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True",
                    "PYTHONPATH": "/app/src",
                }
            )
            self.process = subprocess.Popen(
                self._command(normalized),
                stdout=self.log_stream,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            self.active_stage = normalized
            try:
                self._wait_ready(normalized)
            except Exception as error:
                self.last_error = f"{type(error).__name__}: {error}"
                self.stop()
                raise
            self.last_error = None
            return {"stage": normalized, "changed": True, "pid": self.process.pid}

    def state(self) -> dict[str, object]:
        with self.lock:
            return {
                "ready": True,
                "all_models_ready": self.all_models_ready,
                "active_stage": self.active_stage,
                "worker_pid": self.process.pid if self.process and self.process.poll() is None else None,
                "last_error": self.last_error,
                "models": self.model_status(),
            }


SUPERVISOR = ModelSupervisor()


class Handler(BaseHTTPRequestHandler):
    server_version = "NovelModelSupervisor/1"

    def _send(self, status: int, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path != "/ready":
            self._send(404, {"error": "not found"})
            return
        self._send(200, SUPERVISOR.state())

    def do_POST(self) -> None:
        if self.path != "/stage":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            result = SUPERVISOR.switch(str(payload["stage"]))
            self._send(200, {"success": True, **result})
        except Exception as error:
            self._send(503, {"success": False, "error": f"{type(error).__name__}: {error}"})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)

    def shutdown(_signum: int, _frame: object) -> None:
        SUPERVISOR.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        SUPERVISOR.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
