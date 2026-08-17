#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from image_prompting import (
    LOCAL_IMAGE_PROMPT_POLICY,
    compile_image_prompt,
    infer_reference_mode,
)
from novel_manga.voxcpm_voice import (
    resolve_voice_profile,
    stable_voice_seed,
    styled_clone_text,
    voice_cache_name,
    voice_design_text,
)


def _apply_zimage_runtime_patches() -> None:
    """Keep the pinned Z-Image checkpoint compatible with the image venv."""
    try:
        import torch
        import torch._custom_op.impl

        original_infer = torch._custom_op.impl.infer_schema

        def infer_schema(function, mutates_args):
            if hasattr(function, "__annotations__"):
                function.__annotations__ = {}
            try:
                return original_infer(function, mutates_args)
            except Exception:
                return "() -> ()"

        torch._custom_op.impl.infer_schema = infer_schema
        original_register = torch.library.register_autograd

        def register_autograd(*args, **kwargs):
            try:
                return original_register(*args, **kwargs)
            except Exception:
                return None

        torch.library.register_autograd = register_autograd
    except Exception:
        pass

    import torch.nn.functional as functional

    original_sdpa = functional.scaled_dot_product_attention

    def scaled_dot_product_attention(*args, **kwargs):
        kwargs.pop("enable_gqa", None)
        return original_sdpa(*args, **kwargs)

    functional.scaled_dot_product_attention = scaled_dot_product_attention


def _manifest(path: Path) -> dict[str, Path]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {key: Path(value) for key, value in payload["models"].items()}


def _seed(payload: dict[str, Any]) -> int:
    value = payload.get("seed")
    return int(value) if value is not None else 20260811


class ImageService:
    def __init__(self, stage: str, models: dict[str, Path]) -> None:
        import torch

        self.torch = torch
        self.stage = stage
        model_path = models[stage]
        if stage == "image-base":
            _apply_zimage_runtime_patches()
            from diffusers import ZImagePipeline

            self.pipeline = ZImagePipeline.from_pretrained(
                str(model_path), torch_dtype=torch.bfloat16, local_files_only=True
            )
            # The local Z checkpoint is stable with fp32 VAE decode on A100.
            self.pipeline.vae.to(dtype=torch.float32)
        else:
            from diffusers import QwenImageEditPlusPipeline

            self.pipeline = QwenImageEditPlusPipeline.from_pretrained(
                str(model_path), torch_dtype=torch.bfloat16, local_files_only=True
            )
        qwen_offload = os.getenv("NOVEL_QWEN_IMAGE_OFFLOAD", "").strip().casefold()
        if stage == "image-edit" and qwen_offload in {"group", "leaf"}:
            # This opt-in path lets a style probe coexist with another model on
            # the same A100. Production keeps the faster all-GPU default.
            offload_options: dict[str, Any] = {
                "onload_device": torch.device("cuda"),
                "offload_device": torch.device("cpu"),
                "offload_type": "leaf_level" if qwen_offload == "leaf" else "block_level",
                "use_stream": qwen_offload == "leaf",
                "non_blocking": qwen_offload == "leaf",
            }
            if qwen_offload == "group":
                offload_options["num_blocks_per_group"] = int(
                    os.getenv("NOVEL_QWEN_IMAGE_OFFLOAD_BLOCKS", "1")
                )
            self.pipeline.enable_group_offload(
                **offload_options,
            )
        else:
            self.pipeline.to("cuda")
        if hasattr(self.pipeline, "set_progress_bar_config"):
            self.pipeline.set_progress_bar_config(disable=True)

    @staticmethod
    def _generation_size(width: int, height: int) -> tuple[int, int]:
        if height >= width:
            return 768, 1360
        return 1360, 768

    def invoke(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation != "image":
            raise ValueError(f"image worker does not implement {operation}")
        from PIL import Image

        output = Path(payload["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        requested_width = int(payload.get("width", 1080))
        requested_height = int(payload.get("height", 1920))
        width, height = self._generation_size(requested_width, requested_height)
        generator = self.torch.Generator(device="cuda").manual_seed(_seed(payload))
        policy = str(
            payload.get("prompt_policy")
            or os.getenv("NOVEL_LOCAL_IMAGE_PROMPT_POLICY", LOCAL_IMAGE_PROMPT_POLICY)
        )
        style_profile_value = payload.get("style_profile") or os.getenv(
            "NOVEL_LOCAL_IMAGE_STYLE_PROFILE", ""
        )
        style_profile = str(style_profile_value).strip() or None
        reference = payload.get("reference")
        reference_mode = str(payload.get("reference_mode") or "") or infer_reference_mode(
            reference
        )
        compiled = compile_image_prompt(
            str(payload["prompt"]),
            stage=self.stage,
            policy=policy,
            reference_mode=reference_mode,
            style_profile=style_profile,
        )
        common = {
            "prompt": compiled.positive_prompt,
            "width": width,
            "height": height,
            "generator": generator,
        }
        if self.stage == "image-base":
            result = self.pipeline(
                num_inference_steps=int(os.getenv("NOVEL_Z_IMAGE_STEPS", "9")),
                guidance_scale=0.0,
                **common,
            )
        else:
            if not reference:
                raise ValueError("image-edit requires a reference image")
            with Image.open(reference) as source:
                reference_image = source.convert("RGB")
            result = self.pipeline(
                image=[reference_image],
                negative_prompt=compiled.negative_prompt,
                num_inference_steps=int(os.getenv("NOVEL_QWEN_IMAGE_STEPS", "30")),
                true_cfg_scale=4.0,
                guidance_scale=1.0,
                **common,
            )
        image = result.images[0].convert("RGB")
        image = image.resize(
            (requested_width, requested_height), Image.Resampling.LANCZOS
        )
        image.save(output, format="JPEG", quality=96, subsampling=0)
        return {
            "output": str(output),
            "width": image.width,
            "height": image.height,
            **compiled.audit_payload(),
            "original_prompt_sha256": hashlib.sha256(
                str(payload["prompt"]).encode("utf-8")
            ).hexdigest(),
            "effective_prompt_sha256": hashlib.sha256(
                compiled.positive_prompt.encode("utf-8")
            ).hexdigest(),
        }


class AudioService:
    VOICES = {
        "alloy": "Uncle_Fu",
        "coral": "Serena",
        "verse": "Dylan",
        "sage": "Vivian",
        "ash": "Ryan",
        "nova": "Ono_Anna",
        "echo": "Eric",
        "fable": "Aiden",
        "onyx": "Uncle_Fu",
        "shimmer": "Sohee",
    }

    def __init__(self, models: dict[str, Path], *, load_tts: bool = True) -> None:
        import torch
        from qwen_asr import Qwen3ASRModel, Qwen3ForcedAligner

        self.torch = torch
        self.models = models
        self.tts_backend = os.getenv("NOVEL_TTS_BACKEND", "voxcpm2").strip().casefold()
        if self.tts_backend not in {"voxcpm2", "qwen"}:
            raise ValueError("NOVEL_TTS_BACKEND must be voxcpm2 or qwen")
        self.voxcpm = None
        self.qwen_tts = None
        self._call_counts: dict[str, int] = {}
        self.voice_cache_dir = Path(
            os.getenv("NOVEL_VOXCPM_VOICE_CACHE_DIR", "/output/.runtime/voxcpm2-voices")
        )
        self.voice_cache_dir.mkdir(parents=True, exist_ok=True)
        if load_tts:
            if self.tts_backend == "voxcpm2":
                self._load_voxcpm()
            else:
                self._load_qwen_tts(required=True)
        self.asr = Qwen3ASRModel.from_pretrained(
            str(models["asr"]),
            dtype=torch.bfloat16,
            device_map="cuda:0",
            attn_implementation="sdpa",
            max_inference_batch_size=8,
            max_new_tokens=512,
        )
        self.aligner = Qwen3ForcedAligner.from_pretrained(
            str(models["aligner"]),
            dtype=torch.bfloat16,
            device_map="cuda:0",
            attn_implementation="sdpa",
        )

    def _load_voxcpm(self) -> None:
        from voxcpm import VoxCPM

        self.voxcpm = VoxCPM.from_pretrained(
            str(self.models["tts"]),
            load_denoiser=False,
            local_files_only=True,
            optimize=os.getenv("NOVEL_VOXCPM_OPTIMIZE", "0") == "1",
            device="cuda",
        )

    def _qwen_model_path(self) -> Path | None:
        candidate = self.models.get("tts-qwen")
        if candidate is None and self.tts_backend == "qwen":
            candidate = self.models.get("tts")
        if candidate is None or not (candidate / "config.json").is_file():
            return None
        return candidate

    def _load_qwen_tts(self, *, required: bool) -> bool:
        if self.qwen_tts is not None:
            return True
        model_path = self._qwen_model_path()
        if model_path is None:
            if required:
                raise RuntimeError("Qwen TTS fallback model is not mounted")
            return False
        from qwen_tts import Qwen3TTSModel

        self.qwen_tts = Qwen3TTSModel.from_pretrained(
            str(model_path),
            device_map="cuda:0",
            dtype=self.torch.bfloat16,
            attn_implementation="sdpa",
        )
        return True

    @staticmethod
    def _subtitle_pages(text: str, start: float, end: float) -> list[dict[str, Any]]:
        clean = "".join(text.split())
        pages = [clean[index : index + 36] for index in range(0, len(clean), 36)] or [""]
        total = max(1, sum(len(page) for page in pages))
        cursor = start
        events = []
        for index, page in enumerate(pages):
            if index == len(pages) - 1:
                page_end = end
            else:
                page_end = cursor + (end - start) * len(page) / total
            lines = [page[i : i + 18] for i in range(0, len(page), 18)]
            events.append(
                {
                    "start": round(cursor, 6),
                    "end": round(max(cursor + 0.05, page_end), 6),
                    "text": r"\N".join(lines),
                }
            )
            cursor = page_end
        return events

    @staticmethod
    def _write_waveform(path: Path, wav: Any, sample_rate: int) -> None:
        import numpy as np
        import soundfile as sf

        array = np.asarray(wav, dtype=np.float32).squeeze()
        if array.ndim != 1 or array.size == 0:
            raise RuntimeError(f"TTS returned invalid audio shape: {array.shape}")
        sf.write(path, array, int(sample_rate), subtype="PCM_16")

    def _external_reference(self, voice: str) -> Path | None:
        root = os.getenv("NOVEL_VOXCPM_REFERENCE_DIR")
        if not root:
            return None
        profile = resolve_voice_profile(voice)
        directory = Path(root)
        for stem in (voice, profile.key):
            for suffix in (".wav", ".flac", ".mp3"):
                candidate = directory / f"{stem}{suffix}"
                if candidate.is_file() and candidate.stat().st_size > 0:
                    return candidate
        return None

    def _voice_reference(self, voice: str) -> Path:
        external = self._external_reference(voice)
        if external is not None:
            return external
        if self.voxcpm is None:
            raise RuntimeError("VoxCPM2 is not loaded")
        revision = os.getenv("NOVEL_VOXCPM_VOICE_CACHE_REVISION", "v2-bffb3df5")
        directory = self.voice_cache_dir / revision
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / voice_cache_name(voice)
        if output.is_file() and output.stat().st_size > 44:
            return output
        profile = resolve_voice_profile(voice)
        self.torch.manual_seed(stable_voice_seed(voice, "reference"))
        self.torch.cuda.manual_seed_all(stable_voice_seed(voice, "reference"))
        wav = self.voxcpm.generate(
            text=voice_design_text(profile),
            cfg_value=float(os.getenv("NOVEL_VOXCPM_CFG", "2.0")),
            inference_timesteps=int(os.getenv("NOVEL_VOXCPM_STEPS", "10")),
            max_len=int(os.getenv("NOVEL_VOXCPM_MAX_LEN", "4096")),
            normalize=False,
            retry_badcase=True,
            retry_badcase_max_times=2,
        )
        temporary = output.with_suffix(f".{os.getpid()}.incomplete.wav")
        self._write_waveform(temporary, wav, int(self.voxcpm.tts_model.sample_rate))
        os.replace(temporary, output)
        return output

    def _synthesize_voxcpm_raw(self, payload: dict[str, Any], output: Path) -> dict[str, Any]:
        if self.voxcpm is None:
            raise RuntimeError("VoxCPM2 is not loaded")
        requested_voice = str(payload.get("voice") or "alloy")
        reference = self._voice_reference(requested_voice)
        text = str(payload["text"])
        counter_key = hashlib.sha256(
            f"{requested_voice}\0{text}\0{payload.get('instructions') or ''}".encode("utf-8")
        ).hexdigest()
        attempt = self._call_counts.get(counter_key, 0)
        self._call_counts[counter_key] = attempt + 1
        seed = stable_voice_seed(requested_voice, f"{counter_key}:{attempt}")
        self.torch.manual_seed(seed)
        self.torch.cuda.manual_seed_all(seed)
        wav = self.voxcpm.generate(
            text=styled_clone_text(text, str(payload.get("instructions") or "")),
            reference_wav_path=str(reference),
            cfg_value=float(os.getenv("NOVEL_VOXCPM_CFG", "2.0")),
            inference_timesteps=int(os.getenv("NOVEL_VOXCPM_STEPS", "10")),
            max_len=int(os.getenv("NOVEL_VOXCPM_MAX_LEN", "4096")),
            normalize=False,
            retry_badcase=True,
            retry_badcase_max_times=2,
        )
        self._write_waveform(output, wav, int(self.voxcpm.tts_model.sample_rate))
        return {
            "backend": "voxcpm2-2b-local",
            "voice": resolve_voice_profile(requested_voice).key,
            "reference_audio": str(reference),
        }

    def _synthesize_qwen_raw(self, payload: dict[str, Any], output: Path) -> dict[str, Any]:
        if not self._load_qwen_tts(required=True) or self.qwen_tts is None:
            raise RuntimeError("Qwen3-TTS is not available")
        requested_voice = str(payload.get("voice") or "alloy")
        voice = self.VOICES.get(requested_voice, requested_voice)
        wavs, sample_rate = self.qwen_tts.generate_custom_voice(
            text=str(payload["text"]),
            speaker=voice,
            language="Chinese",
            instruct=str(payload.get("instructions") or "标准普通话，自然清晰，音色稳定。"),
        )
        if not wavs or len(wavs[0]) == 0:
            raise RuntimeError("Qwen3-TTS returned empty audio")
        self._write_waveform(output, wavs[0], int(sample_rate))
        return {"backend": "qwen3-tts-1.7b-local", "voice": voice}

    def _postprocess_tts(
        self,
        source: Path,
        output: Path,
        requested_speed: float | None = None,
    ) -> float:
        speed = float(
            requested_speed
            if requested_speed is not None
            else os.getenv("NOVEL_TTS_SPEED", "1.25")
        )
        if not 0.5 <= speed <= 2.0:
            raise ValueError("NOVEL_TTS_SPEED must be between 0.5 and 2.0")
        speed_filters = []
        if abs(speed - 1.0) >= 0.005:
            speed_filters.append(f"atempo={speed:.4f}")
        analysis_filters = [*speed_filters, "ebur128=peak=true"]
        analysis = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats", "-i", str(source),
                "-filter:a", ",".join(analysis_filters), "-f", "null", "-",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        loudness_values = re.findall(
            r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS",
            analysis.stderr,
        )
        input_lufs = float(loudness_values[-1]) if loudness_values else -18.0
        gain_db = min(24.0, max(-12.0, -18.0 - input_lufs))
        filters = [
            *speed_filters,
            f"volume={gain_db:.2f}dB",
            "alimiter=limit=0.8414:level=false:attack=5:release=50",
        ]
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                "-filter:a",
                ",".join(filters),
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                str(output),
            ],
            check=True,
        )
        return speed

    def _tts(self, payload: dict[str, Any]) -> dict[str, Any]:
        output = Path(payload["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="local-tts-") as directory:
            raw = Path(directory) / "raw.wav"
            if self.tts_backend == "qwen":
                result = self._synthesize_qwen_raw(payload, raw)
            else:
                try:
                    result = self._synthesize_voxcpm_raw(payload, raw)
                except Exception as error:
                    allow_fallback = os.getenv("NOVEL_TTS_QWEN_FALLBACK", "1") == "1"
                    if not allow_fallback or not self._load_qwen_tts(required=False):
                        raise
                    print(
                        f"VoxCPM2 synthesis failed; falling back to Qwen3-TTS: "
                        f"{type(error).__name__}: {error}",
                        flush=True,
                    )
                    result = self._synthesize_qwen_raw(payload, raw)
                    result["fallback_reason"] = f"{type(error).__name__}: {error}"[:500]
            requested_speed = payload.get("speed")
            speed = self._postprocess_tts(
                raw,
                output,
                float(requested_speed) if requested_speed is not None else None,
            )
        return {"output": str(output), "speed": speed, **result}

    def _asr(self, payload: dict[str, Any]) -> dict[str, Any]:
        results = self.asr.transcribe(
            audio=str(payload["audio"]),
            context=str(payload.get("text") or ""),
            language="Chinese",
            return_time_stamps=False,
        )
        if not results:
            raise RuntimeError("Qwen3-ASR returned no result")
        return {"backend": "qwen3-asr-1.7b-local", "hypothesis": results[0].text}

    def _align(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload["text"])
        results = self.aligner.align(
            audio=str(payload["audio"]), text=text, language="Chinese"
        )
        if not results or not results[0]:
            raise RuntimeError("Qwen3 ForcedAligner returned no timestamp")
        start = float(results[0][0].start_time)
        end = float(results[0][-1].end_time)
        return {
            "backend": "qwen3-forced-aligner-0.6b-local",
            "speech_start": start,
            "speech_end": end,
            "events": self._subtitle_pages(text, start, end),
        }

    def invoke(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation == "tts":
            return self._tts(payload)
        if operation == "asr":
            return self._asr(payload)
        if operation == "align":
            return self._align(payload)
        raise ValueError(f"audio worker does not implement {operation}")


def build_service(stage: str, models: dict[str, Path]):
    if stage in {"image-base", "image-edit"}:
        return ImageService(stage, models)
    if stage in {"audio", "audio-evidence"}:
        return AudioService(models, load_tts=stage == "audio")
    raise ValueError(f"unsupported worker stage: {stage}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18100)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    service = build_service(args.stage, _manifest(args.manifest))
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            if self.path == "/ready":
                self._send(200, {"ready": True, "stage": args.stage})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/invoke":
                self._send(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length))
                with lock:
                    result = service.invoke(str(request["operation"]), dict(request["payload"]))
                self._send(200, {"success": True, "result": result})
            except Exception as error:
                self._send(500, {"success": False, "error": f"{type(error).__name__}: {error}"})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
