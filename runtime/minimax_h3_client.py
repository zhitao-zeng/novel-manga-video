#!/usr/bin/env python3
"""ComfyUI client for local MiniMax H3 Ref2VA novel-drama shots."""

from __future__ import annotations

import json
import hashlib
import math
import mimetypes
import os
import random
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx


class MiniMaxH3Error(RuntimeError):
    """Raised when a MiniMax H3 ComfyUI job cannot be completed."""


def stable_generation_seed(
    *,
    prompt: str,
    image_paths: tuple[Path, ...],
    audio_path: Path,
    duration_seconds: float,
) -> int:
    """Derive a reproducible H3 seed from the complete conditioning inputs."""

    digest = hashlib.sha256()
    digest.update(prompt.encode("utf-8"))
    digest.update(f"\0{duration_seconds:.6f}\0".encode("ascii"))
    for path in (*image_paths, audio_path):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return int.from_bytes(digest.digest()[:8], "big") & ((1 << 63) - 1)


H3_PROMPT_COMPILER_REVISION = "h3-drama-v2-camera-contract"
H3_VIDEO_SIGMA_SHIFT = 12.0
H3_AUDIO_SIGMA_SHIFT = 3.0


@dataclass(frozen=True)
class MiniMaxH3Config:
    server_url: str = "http://127.0.0.1:18100"
    model: str = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    model_revision: str = ""
    text_encoder: str = "qwen3vl_32b_minimax_h3_int8_convrot.safetensors"
    video_vae: str = "minimax_h3_video_vae_fp16.safetensors"
    audio_vae: str = "minimax_h3_audio_vae_fp32.safetensors"
    width: int = 480
    height: int = 832
    steps: int = 20
    sampler: str = "res_multistep"
    scheduler: str = "simple"
    ref_image_size: str = "match"
    use_sageattention: bool = False
    poll_interval: float = 5.0
    timeout_seconds: float = 7200.0

    @classmethod
    def from_env(cls) -> "MiniMaxH3Config":
        return cls(
            server_url=os.getenv("NOVEL_MINIMAX_H3_URL", cls.server_url),
            model=os.getenv("NOVEL_MINIMAX_H3_MODEL", cls.model),
            model_revision=os.getenv(
                "NOVEL_MINIMAX_H3_MODEL_REVISION", cls.model_revision
            ),
            text_encoder=os.getenv(
                "NOVEL_MINIMAX_H3_TEXT_ENCODER", cls.text_encoder
            ),
            video_vae=os.getenv("NOVEL_MINIMAX_H3_VIDEO_VAE", cls.video_vae),
            audio_vae=os.getenv("NOVEL_MINIMAX_H3_AUDIO_VAE", cls.audio_vae),
            width=int(os.getenv("NOVEL_MINIMAX_H3_WIDTH", str(cls.width))),
            height=int(os.getenv("NOVEL_MINIMAX_H3_HEIGHT", str(cls.height))),
            steps=int(os.getenv("NOVEL_MINIMAX_H3_STEPS", str(cls.steps))),
            sampler=os.getenv("NOVEL_MINIMAX_H3_SAMPLER", cls.sampler),
            scheduler=os.getenv("NOVEL_MINIMAX_H3_SCHEDULER", cls.scheduler),
            ref_image_size=os.getenv(
                "NOVEL_MINIMAX_H3_REF_IMAGE_SIZE", cls.ref_image_size
            ),
            use_sageattention=os.getenv(
                "NOVEL_MINIMAX_H3_SAGEATTENTION", "0"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            poll_interval=float(
                os.getenv("NOVEL_MINIMAX_H3_POLL_INTERVAL", str(cls.poll_interval))
            ),
            timeout_seconds=float(
                os.getenv("NOVEL_MINIMAX_H3_TIMEOUT", str(cls.timeout_seconds))
            ),
        )

    def audit_identity(self) -> dict[str, Any]:
        """Return the exact checkpoint and sampling bundle used by ComfyUI."""

        return {
            "backend": "MiniMax-H3-Ref2VA",
            "model": self.model,
            "model_revision": self.model_revision or None,
            "steps": self.steps,
            "sampler": self.sampler,
            "scheduler": self.scheduler,
            "sigma_shift_video": H3_VIDEO_SIGMA_SHIFT,
            "sigma_shift_audio": H3_AUDIO_SIGMA_SHIFT,
        }


def aligned_frame_count(duration_seconds: float, fps: int = 24) -> int:
    """Return H3's required 17k+5 frame count without shortening audio."""

    frames = max(5, math.ceil(duration_seconds * fps))
    while frames % 17 != 5:
        frames += 1
    return frames


def build_drama_prompt(prompt: str, *, picture_count: int = 1) -> str:
    """Wrap the director prompt with explicit H3 reference-media semantics."""

    if not 1 <= picture_count <= 9:
        raise ValueError("MiniMax H3 requires between one and nine reference pictures")
    if picture_count == 1:
        picture_definitions = (
            "<Picture 1> is the exact identity, costume, environment, art-style and "
            "starting-composition anchor for this shot."
        )
        picture_retention = (
            "<Picture 1>: fully_preserved for identity, costume, environment, art style, "
            "lighting direction and starting spatial relationships. Do not freeze its pose "
            "or camera position when the director prompt requests motivated movement."
        )
        merge_instruction = "from <Picture 1>"
    else:
        extra_definitions = "\n".join(
            f"<Picture {index}> is an additional explicitly described reusable asset; "
            "use only the role assigned to it in the director prompt."
            for index in range(3, picture_count + 1)
        )
        picture_definitions = (
            "<Picture 1> is the character identity asset. Preserve only the face, age, "
            "hair, body design, costume, materials and character rendering style; its "
            "studio background, pose and framing are not part of the scene.\n"
            "<Picture 2> is the empty environment asset. Preserve only its architecture, "
            "layout, key props, palette, lighting direction and environment rendering "
            "style; it contains no foreground actor."
        )
        if extra_definitions:
            picture_definitions += "\n" + extra_definitions
        picture_retention = (
            "<Picture 1>: fully_preserved for the character identity and costume only; "
            "discard its studio background and static pose.\n"
            "<Picture 2>: fully_preserved for environment geometry, props, palette and "
            "lighting only. Place the character from <Picture 1> naturally inside this "
            "single continuous environment."
        )
        merge_instruction = "by compositing the reusable assets into one coherent scene"

    opening_frame_contract = (
        "At frame 0, <Picture 1> is already the exact composed starting frame. "
        "Preserve its visible character, environment, framing and screen position "
        "immediately, with the mouth naturally closed before speech. Never replace "
        "it with an empty establishing shot, a character entrance, a fade-in, a "
        "dissolve or a delayed identity reveal."
        if picture_count == 1
        else (
            "At frame 0, the character from <Picture 1> is already fully composited "
            "inside <Picture 2> at the director-specified framing and screen position, "
            "with the mouth naturally closed before speech. The opening frame is part "
            "of the acted shot: never begin with an empty environment, an establishing "
            "shot, a character entrance from outside frame, a fade-in, a dissolve or a "
            "delayed identity reveal."
        )
    )
    locked_camera = any(
        marker in prompt
        for marker in (
            "模式=locked",
            "摄影机全程保持完全静止",
            "锁定机位",
        )
    )
    camera_instruction = (
        "The director CameraPlan is locked. Keep the physical camera completely "
        "stationary for the whole shot: no pan, tilt, dolly, truck, orbit, crane, "
        "reframing or digital zoom. Preserve one composition and create motion only "
        "through the actor, hair, clothing and subtle environmental response."
        if locked_camera
        else (
            "Follow only the physical camera trajectory explicitly specified in the "
            "director CameraPlan. Move through the same 3D environment with the stated "
            "parallax; never add a second move or use a pure digital zoom."
        )
    )

    return f"""subject_definitions:
{picture_definitions}
<Audio 1> is the exact visible-dialogue performance track. Silent spans are narration, off-screen speech or intentional pauses and must not create lip movement.

summary:
[reference generation + audio reuse] Generate one continuous vertical Chinese manga-drama performance {merge_instruction}. Follow the purposeful action and camera beats below while preserving character identity, costume, scene geometry and art style.

opening_frame_contract:
{opening_frame_contract}

retention_analysis:
{picture_retention}
<Audio 1>: fully_copy for timing and visible speech only. Preserve phonemes and pauses exactly; never invent speech, music or sound effects.

detailed_description:
{prompt}

This is a performed scene, not an animated still image. Use action-reaction-action progression and clear motion beats every 1-2 seconds. Eyes move before the head, the head before the shoulders, and hair and clothing react slightly later. {camera_instruction} Do not cut, reset character positions, mirror the scene, duplicate a person, change costume, change art style, add readable text, or hold the initial pose for the entire shot.

overall_soundscape:
Use <Audio 1> unchanged as the complete timing track. Visible speakers articulate only during their audible dialogue. During silence every visible character keeps a naturally closed mouth while continuing non-speaking performance. Add no replacement dialogue, ambience, music or sound effects.

non_diegetic_music:
None."""


class MiniMaxH3Client:
    def __init__(self, config: MiniMaxH3Config):
        if config.width % 32 or config.height % 32:
            raise ValueError("MiniMax H3 width and height must be divisible by 32")
        if config.steps < 1:
            raise ValueError("MiniMax H3 steps must be positive")
        if config.sampler not in {"res_multistep", "euler"}:
            raise ValueError("MiniMax H3 sampler must be res_multistep or euler")
        if config.scheduler not in {"simple", "beta", "normal"}:
            raise ValueError("MiniMax H3 scheduler must be simple, beta, or normal")
        if config.ref_image_size not in {"match", "max"}:
            raise ValueError("MiniMax H3 reference image size must be match or max")
        self.config = config
        self.server_url = config.server_url.rstrip("/")
        self.client_id = f"novel-manga-{uuid.uuid4().hex}"
        # ComfyUI is a local/private service; inherited public proxies break it.
        self.session = httpx.Client(trust_env=False)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self.session.request(
                method,
                self.server_url + path,
                timeout=kwargs.pop("timeout", 60),
                **kwargs,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError as error:
            detail = ""
            if getattr(error, "response", None) is not None:
                detail = error.response.text[:2000]
            message = f"ComfyUI request failed: {method} {path}: {error}"
            if detail:
                message += f"\n{detail}"
            raise MiniMaxH3Error(message) from error

    def preflight(self) -> None:
        required_nodes = [
            "MiniMaxH3ReferenceToVideo",
            "VRGDG_MiniMaxH3AudioDrive",
            "MiniMaxH3SigmaShift",
            "SaveVideo",
        ]
        if self.config.use_sageattention:
            required_nodes.append("MiniMaxH3MemoryEfficientSageAttentionPatch")
        missing = []
        for node_name in required_nodes:
            data = self._request(
                "GET", f"/object_info/{node_name}", timeout=30
            ).json()
            if node_name not in data:
                missing.append(node_name)
        if missing:
            raise MiniMaxH3Error(
                "ComfyUI is missing required MiniMax H3 nodes: "
                + ", ".join(missing)
            )

    def upload(self, path: Path) -> str:
        upload_name = f"{uuid.uuid4().hex}_{path.name}"
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as handle:
            result = self._request(
                "POST",
                "/upload/image",
                files={"image": (upload_name, handle, mime)},
                data={
                    "type": "input",
                    "subfolder": "novel-manga/minimax-h3",
                    "overwrite": "false",
                },
                timeout=300,
            ).json()
        name = result.get("name")
        if not name:
            raise MiniMaxH3Error(f"ComfyUI upload returned no filename: {result}")
        subfolder = result.get("subfolder", "")
        return f"{subfolder}/{name}" if subfolder else str(name)

    def build_graph(
        self,
        *,
        image_name: str,
        additional_image_names: tuple[str, ...] = (),
        audio_name: str,
        duration_seconds: float,
        prompt: str,
        output_prefix: str,
        seed: int | None = None,
    ) -> dict[str, dict[str, Any]]:
        seed = seed if seed is not None else random.randrange(0, 2**63 - 1)
        image_names = (image_name, *additional_image_names)
        if len(image_names) > 9:
            raise ValueError("MiniMax H3 accepts at most nine reference pictures")
        model_output: list[Any] = ["2", 0]
        graph: dict[str, dict[str, Any]] = {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": self.config.model, "weight_dtype": "default"},
            },
            "2": {
                "class_type": "MiniMaxH3SigmaShift",
                "inputs": {
                    "model": ["1", 0],
                    "shift_video": H3_VIDEO_SIGMA_SHIFT,
                    "shift_audio": H3_AUDIO_SIGMA_SHIFT,
                },
            },
            "4": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": self.config.text_encoder,
                    "type": "minimax",
                    "device": "default",
                },
            },
            "5": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": self.config.video_vae},
            },
            "6": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": self.config.audio_vae},
            },
            "7": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "8": {"class_type": "LoadAudio", "inputs": {"audio": audio_name}},
            "9": {
                "class_type": "MiniMaxH3ReferenceToVideo",
                "inputs": {
                    "clip": ["4", 0],
                    "vae": ["5", 0],
                    "audio_vae": ["6", 0],
                    "prompt": build_drama_prompt(prompt, picture_count=len(image_names)),
                    "width": self.config.width,
                    "height": self.config.height,
                    "length": aligned_frame_count(duration_seconds),
                    "ref_image_size": self.config.ref_image_size,
                    "ref_images.ref_image_0": ["7", 0],
                    "ref_audios.ref_audio_0": ["8", 0],
                },
            },
            "10": {
                "class_type": "VRGDG_MiniMaxH3AudioDrive",
                "inputs": {
                    "av_latent": ["9", 1],
                    "source_audio": ["8", 0],
                    "audio_vae": ["6", 0],
                },
            },
            "12": {
                "class_type": "BasicGuider",
                "inputs": {"model": model_output, "conditioning": ["9", 0]},
            },
            "13": {
                "class_type": "KSamplerSelect",
                "inputs": {"sampler_name": self.config.sampler},
            },
            "14": {
                "class_type": "BasicScheduler",
                "inputs": {
                    "model": model_output,
                    "scheduler": self.config.scheduler,
                    "steps": self.config.steps,
                    "denoise": 1.0,
                },
            },
            "15": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
            "16": {
                "class_type": "SamplerCustomAdvanced",
                "inputs": {
                    "noise": ["15", 0],
                    "guider": ["12", 0],
                    "sampler": ["13", 0],
                    "sigmas": ["14", 0],
                    "latent_image": ["10", 0],
                },
            },
            "17": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["16", 0], "vae": ["5", 0]},
            },
            "18": {
                "class_type": "CreateVideo",
                "inputs": {
                    "images": ["17", 0],
                    "audio": ["10", 1],
                    "fps": 24.0,
                    "bit_depth": 8,
                },
            },
            "19": {
                "class_type": "SaveVideo",
                "inputs": {
                    "video": ["18", 0],
                    "filename_prefix": output_prefix,
                    "format": "auto",
                    "codec": "auto",
                },
            },
        }
        for index, extra_name in enumerate(additional_image_names, start=1):
            node_id = str(19 + index)
            graph[node_id] = {
                "class_type": "LoadImage",
                "inputs": {"image": extra_name},
            }
            graph["9"]["inputs"][f"ref_images.ref_image_{index}"] = [node_id, 0]
        if self.config.use_sageattention:
            graph["3"] = {
                "class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch",
                "inputs": {"model": ["2", 0]},
            }
            model_output = ["3", 0]
            graph["12"]["inputs"]["model"] = model_output
            graph["14"]["inputs"]["model"] = model_output
        return graph

    def submit(self, graph: dict[str, dict[str, Any]]) -> str:
        result = self._request(
            "POST",
            "/prompt",
            json={"prompt": graph, "client_id": self.client_id},
            timeout=120,
        ).json()
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            raise MiniMaxH3Error(
                "ComfyUI rejected the prompt: "
                + json.dumps(result, ensure_ascii=False)[:4000]
            )
        node_errors = result.get("node_errors") or {}
        if node_errors:
            raise MiniMaxH3Error(
                "ComfyUI prompt validation failed: "
                + json.dumps(node_errors, ensure_ascii=False)[:4000]
            )
        return str(prompt_id)

    def wait_for_output(self, prompt_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.timeout_seconds
        while time.monotonic() < deadline:
            history = self._request(
                "GET", f"/history/{prompt_id}", timeout=60
            ).json()
            if prompt_id in history:
                result = history[prompt_id]
                status = result.get("status", {})
                if status.get("status_str") != "success":
                    raise MiniMaxH3Error(
                        "MiniMax H3 generation failed: "
                        + json.dumps(
                            status.get("messages", [])[-10:], ensure_ascii=False
                        )[:5000]
                    )
                return result
            time.sleep(self.config.poll_interval)
        raise MiniMaxH3Error(
            "MiniMax H3 generation timed out after "
            f"{self.config.timeout_seconds / 60:.0f} minutes"
        )

    @staticmethod
    def _find_video(result: dict[str, Any]) -> dict[str, str]:
        for output in result.get("outputs", {}).values():
            for value in output.values():
                if not isinstance(value, list):
                    continue
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    filename = str(item.get("filename", ""))
                    if filename.lower().endswith((".mp4", ".webm", ".mov", ".mkv")):
                        return {
                            "filename": filename,
                            "subfolder": str(item.get("subfolder", "")),
                            "type": str(item.get("type", "output")),
                        }
        raise MiniMaxH3Error("ComfyUI completed the job but returned no video")

    def download_video(self, artifact: dict[str, str], destination: Path) -> Path:
        response = self._request(
            "GET", f"/view?{urlencode(artifact)}", timeout=300
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".partial")
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        partial.replace(destination)
        return destination

    def generate(
        self,
        *,
        image_path: Path,
        additional_image_paths: tuple[Path, ...] = (),
        audio_path: Path,
        duration_seconds: float,
        prompt: str,
        destination: Path,
        seed: int | None = None,
    ) -> Path:
        self.preflight()
        raw = destination.with_suffix(".h3-raw.mp4")
        image_name = self.upload(image_path)
        additional_image_names = tuple(
            self.upload(path) for path in additional_image_paths
        )
        audio_name = self.upload(audio_path)
        graph = self.build_graph(
            image_name=image_name,
            additional_image_names=additional_image_names,
            audio_name=audio_name,
            duration_seconds=duration_seconds,
            prompt=prompt,
            output_prefix=(
                f"novel-manga/minimax-h3/{destination.stem}_{uuid.uuid4().hex[:8]}"
            ),
            seed=seed,
        )
        prompt_id = self.submit(graph)
        result = self.wait_for_output(prompt_id)
        self.download_video(self._find_video(result), raw)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(raw),
                    "-map",
                    "0:v:0",
                    "-t",
                    f"{duration_seconds:.6f}",
                    "-an",
                    "-r",
                    "24",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    str(destination),
                ],
                check=True,
            )
        finally:
            raw.unlink(missing_ok=True)
        return destination
