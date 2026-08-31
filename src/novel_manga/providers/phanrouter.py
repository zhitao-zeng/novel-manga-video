from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from ..config import Settings
from ..util import atomic_write_json, retry
from .base import ImageResult, MediaProvider


class PhanRouterMediaProvider(MediaProvider):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(timeout=settings.request_timeout)
        self.video_headers = {
            "Authorization": f"Bearer {settings.phanrouter_api_key}"
        }
        self.image_headers = {
            "Authorization": (
                f"Bearer {settings.phanrouter_image_api_key or settings.phanrouter_api_key}"
            )
        }

    def _download(self, url: str, output: Path, max_bytes: int = 512 * 1024 * 1024) -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_suffix(output.suffix + ".partial")
        with self.client.stream("GET", url, follow_redirects=True) as response:
            response.raise_for_status()
            total = 0
            with partial.open("wb") as stream:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"remote artifact exceeds {max_bytes} bytes")
                    stream.write(chunk)
        os.replace(partial, output)

    @staticmethod
    def _task_data(payload: dict) -> dict:
        result = payload.get("Result") or payload.get("data") or payload
        return result if isinstance(result, dict) else payload

    def _poll_image_url(self, task_id: str) -> str:
        deadline = time.monotonic() + self.settings.poll_timeout
        while time.monotonic() < deadline:
            response = self.client.get(
                f"{self.settings.phanrouter_base_url.rstrip('/')}/v3/images/generations/{task_id}",
                headers=self.image_headers,
            )
            response.raise_for_status()
            data = self._task_data(response.json())
            status = str(data.get("status", "")).lower()
            if status in {"succeeded", "success"}:
                url = data.get("url")
                if not url:
                    raise ValueError("successful image task returned no URL")
                return str(url)
            if status in {"failed", "failure", "cancelled"}:
                raise RuntimeError(f"image generation failed: {data}")
            time.sleep(5)
        raise TimeoutError(f"image task timed out: {task_id}")

    def _restore_image_url(self, image: ImageResult) -> str:
        if image.public_url:
            return image.public_url
        if self.settings.inline_reference_images:
            if not image.path.is_file():
                raise FileNotFoundError(image.path)
            with Image.open(image.path) as source:
                normalized = ImageOps.exif_transpose(source).convert("RGB")
                normalized = ImageOps.fit(
                    normalized,
                    (720, 1280),
                    method=Image.Resampling.LANCZOS,
                )
                encoded = io.BytesIO()
                normalized.save(encoded, format="JPEG", quality=82, optimize=True)
            payload = encoded.getvalue()
            if len(payload) > 10 * 1024 * 1024:
                raise ValueError("Seedance inline reference image exceeds 10 MiB")
            return f"data:image/jpeg;base64,{base64.b64encode(payload).decode('ascii')}"
        task_path = image.path.with_suffix(image.path.suffix + ".task.json")
        if not task_path.is_file():
            raise ValueError("PhanRouter image task metadata is missing; cannot restore provider reference")
        task_id = json.loads(task_path.read_text(encoding="utf-8")).get("task_id")
        if not task_id:
            raise ValueError("PhanRouter image task metadata has no task_id")
        return self._poll_image_url(str(task_id))

    def create_image(
        self,
        prompt: str,
        output: Path,
        reference: Path | None = None,
        additional_references: tuple[Path, ...] = (),
    ) -> ImageResult:
        if self.settings.image_model in {
            "doubao-seedream-5.0-lite",
            "doubao-seedream-4.5",
        }:
            if additional_references:
                raise ValueError("Seedream image generation accepts only one reference")
            return self._create_seedream_image(prompt, output, reference)

        payload: dict[str, object] = {
            "model": self.settings.image_model,
            "prompt": prompt,
            "aspectRatio": "9:16",
            "resolution": "2K",
            "thinking": "high",
        }
        if reference and additional_references:
            payload["base64Files"] = [
                base64.b64encode(path.read_bytes()).decode("ascii")
                for path in (reference, *additional_references)
            ]
        elif reference:
            payload["base64File"] = base64.b64encode(reference.read_bytes()).decode("ascii")

        def submit() -> httpx.Response:
            response = self.client.post(
                f"{self.settings.phanrouter_base_url.rstrip('/')}/v3/images/generations",
                headers=self.image_headers, json=payload,
            )
            response.raise_for_status()
            return response

        task_path = output.with_suffix(output.suffix + ".task.json")
        if task_path.exists():
            import json
            task_id = json.loads(task_path.read_text(encoding="utf-8")).get("task_id")
        else:
            task_id = retry(submit).json().get("task_id")
            if task_id:
                atomic_write_json(task_path, {"task_id": task_id, "kind": "image"})
        if not task_id:
            raise ValueError("image API returned no task_id")
        try:
            url = self._poll_image_url(str(task_id))
        except RuntimeError:
            task_path.unlink(missing_ok=True)
            raise
        self._download(url, output, max_bytes=64 * 1024 * 1024)
        return ImageResult(path=output, public_url=url)

    def _create_seedream_image(
        self,
        prompt: str,
        output: Path,
        reference: Path | None = None,
    ) -> ImageResult:
        payload: dict[str, object] = {
            "model": self.settings.image_model,
            "prompt": prompt,
            "n": 1,
            "size": "1080x1920",
            "watermark": False,
        }
        if reference is not None:
            if not reference.is_file():
                raise FileNotFoundError(reference)
            with Image.open(reference) as source:
                normalized = ImageOps.exif_transpose(source).convert("RGB")
                encoded = io.BytesIO()
                normalized.save(encoded, format="JPEG", quality=92, optimize=True)
            payload["image"] = (
                "data:image/jpeg;base64,"
                + base64.b64encode(encoded.getvalue()).decode("ascii")
            )

        def submit() -> httpx.Response:
            response = self.client.post(
                f"{self.settings.phanrouter_base_url.rstrip('/')}/v1/images/generations",
                headers=self.image_headers,
                json=payload,
            )
            response.raise_for_status()
            return response

        response = retry(submit)
        data = response.json().get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise ValueError("Seedream image API returned no data")
        url = data[0].get("url")
        if not url:
            raise ValueError("Seedream image API returned no URL")
        self._download(str(url), output, max_bytes=64 * 1024 * 1024)
        atomic_write_json(
            output.with_suffix(output.suffix + ".task.json"),
            {
                "kind": "image",
                "model": self.settings.image_model,
                "endpoint": "/v1/images/generations",
                "request_sha256": hashlib.sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            },
        )
        return ImageResult(path=output, public_url=str(url))

    def _video_payload(
        self,
        prompt: str,
        image_url: str | None,
        duration: float,
        additional_image_urls: tuple[str, ...] = (),
    ) -> dict:
        content = [{"type": "text", "text": prompt}]
        if image_url is not None:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                    "role": "reference_image",
                }
            )
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": additional_url},
                "role": "reference_image",
            }
            for additional_url in additional_image_urls
        )
        return {
            "model": self.settings.video_model,
            "content": content,
            "ratio": "9:16",
            "resolution": "720p",
            "duration": max(4, min(30, math.ceil(duration))),
            "generate_audio": True,
            "watermark": False,
            "output_format": "mp4",
        }

    def create_video(
        self,
        prompt: str,
        image: ImageResult | None,
        output: Path,
        duration: float,
        additional_images: tuple[Path, ...] = (),
    ) -> Path:
        for additional_image in additional_images:
            if not additional_image.is_file():
                raise FileNotFoundError(additional_image)
        image_url = self._restore_image_url(image) if image is not None else None
        additional_image_urls = tuple(
            self._restore_image_url(ImageResult(path=path))
            for path in additional_images
        )
        payload = self._video_payload(
            prompt,
            image_url,
            duration,
            additional_image_urls,
        )
        request_sha256 = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

        def submit() -> httpx.Response:
            response = self.client.post(
                f"{self.settings.phanrouter_base_url.rstrip('/')}/api/v3/contents/generations/tasks",
                headers=self.video_headers, json=payload,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                detail = response.text.strip().replace("\n", " ")[:1000]
                raise RuntimeError(
                    f"Seedance task submission returned HTTP {response.status_code}: {detail}"
                ) from error
            return response

        task_path = output.with_suffix(output.suffix + ".task.json")
        if task_path.exists():
            cached = json.loads(task_path.read_text(encoding="utf-8"))
            if cached.get("request_sha256") != request_sha256:
                raise RuntimeError(f"cached video task request does not match current request: {task_path}")
            task_id = cached.get("task_id")
        else:
            task_id = retry(submit).json().get("task_id")
            if task_id:
                atomic_write_json(
                    task_path,
                    {
                        "task_id": task_id,
                        "kind": "video",
                        "model": self.settings.video_model,
                        "output_format": "mp4",
                        "request_sha256": request_sha256,
                        "additional_image_sha256s": [
                            hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in additional_images
                        ],
                        "generate_audio": bool(payload["generate_audio"]),
                    },
                )
        if not task_id:
            raise ValueError("video API returned no task_id")
        deadline = time.monotonic() + self.settings.poll_timeout
        while time.monotonic() < deadline:
            response = self.client.get(
                f"{self.settings.phanrouter_base_url.rstrip('/')}/api/v3/contents/generations/tasks/{task_id}",
                headers=self.video_headers,
            )
            response.raise_for_status()
            data = self._task_data(response.json())
            status = str(data.get("status", "")).lower()
            if status in {"succeeded", "success"}:
                url = data.get("url") or data.get("video_url")
                if not url:
                    raise ValueError("successful video task returned no URL")
                try:
                    self._download(str(url), output)
                except httpx.TimeoutException:
                    # The generation is already paid and succeeded; a slow
                    # proxy/CDN read must resume the same task rather than
                    # allocate a new generation attempt.
                    time.sleep(5)
                    continue
                except httpx.HTTPStatusError as error:
                    # A newly succeeded Seedance task can be visible in the
                    # task API a few seconds before its signed CDN object is
                    # replicated. Keep polling this same paid task instead of
                    # consuming a fresh generation attempt.
                    if error.response.status_code == 404:
                        time.sleep(5)
                        continue
                    raise
                return output
            if status in {"failed", "failure", "cancelled"}:
                task_path.unlink(missing_ok=True)
                raise RuntimeError(f"video generation failed: {data}")
            time.sleep(5)
        raise TimeoutError(f"video task timed out: {task_id}")
