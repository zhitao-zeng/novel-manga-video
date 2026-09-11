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
from ..util import atomic_write_json
from .base import ImageResult, MediaProvider


SUBMIT_TIMEOUT_SECONDS = 120.0  # a task submission answers in seconds; downloads keep the long request timeout
RESUBMIT_ENV = "NOVEL_RESUBMIT_UNCONFIRMED"  # "1" (thin_batch --resubmit-unconfirmed) once someone has checked the bill


def unconfirmed(record: dict) -> bool:
    """A submission recorded as unconfirmed and not yet allowed to go out again."""
    return bool(record.get("submit_uncertain_at")) and not record.get("task_id") and os.environ.get(RESUBMIT_ENV) != "1"


def held_message(record: dict) -> str:
    return (f"this request's submission at {time.strftime('%m-%d %H:%M', time.localtime(float(record['submit_uncertain_at'])))} went "
            "unconfirmed and may have created a paid task: not sent again until someone checks the bill and reruns with "
            "--resubmit-unconfirmed")


class SubmissionUncertain(RuntimeError):
    """A task submission whose answer was lost after the request went out.  The service may have created the
    task, and billed it, so the request is not sent again automatically."""


def submit_once(submit, attempts: int = 3, base_delay: float = 1.0) -> httpx.Response:
    """Send a task-creating request, retrying only when no task can have been created: the connection was never
    made, or the service refused with 429.  retry() resent on any error, so a submission the service had accepted
    but whose answer timed out was created - and paid for - twice.  A lost answer (read timeout, dropped
    connection) or a gateway timeout raises SubmissionUncertain instead."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return submit()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
            last = error
        except httpx.TransportError as error:
            raise SubmissionUncertain(f"task submission unconfirmed ({type(error).__name__}): not sent again, the service may have accepted it") from error
        except (RuntimeError, httpx.HTTPStatusError) as error:
            status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
            if status == 504 or "HTTP 504" in str(error):
                raise SubmissionUncertain("task submission unconfirmed (gateway timeout 504): not sent again, the service may have accepted it") from error
            if status != 429 and "HTTP 429" not in str(error):
                raise
            last = error
        if attempt + 1 < attempts:
            time.sleep(base_delay * (2 ** attempt))
    assert last is not None
    raise last


# Per-model limits of the tasks endpoint, applied to every request regardless of
# the frame or tier: the self-hosted H3-Base service ("MiniMax-H3") rejects any
# resolution but 768P and any duration outside 4-15 s (probed 2026-09-09).
# Seedance models take the caller's resolution and up to 30 s.
VIDEO_MODEL_LIMITS: dict[str, dict[str, object]] = {
    "MiniMax-H3": {"resolution": "768P", "max_duration": 15},
}


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
        # The CDN sometimes drops a stream part-way through ("peer closed
        # connection without sending complete message body").  The artifact is
        # already generated and paid for, so read it again a few times before
        # giving the failure to the caller.
        for attempt in range(1, 4):
            try:
                with self.client.stream("GET", url, follow_redirects=True) as response:
                    response.raise_for_status()
                    total = 0
                    with partial.open("wb") as stream:
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise ValueError(f"remote artifact exceeds {max_bytes} bytes")
                            stream.write(chunk)
                break
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout, httpx.ConnectError):
                partial.unlink(missing_ok=True)
                if attempt == 3:
                    raise
                time.sleep(3 * attempt)
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

    @staticmethod
    def _submit_recorded(submit, task_path: Path, record: dict) -> str | None:
        """submit_once; an unconfirmed submission is written beside the output, so it is not sent again unasked."""
        try:
            return submit_once(submit).json().get("task_id")
        except SubmissionUncertain:
            atomic_write_json(task_path, {**record, "submit_uncertain_at": time.time()})
            raise

    def _restore_image_url(self, image: ImageResult) -> str:
        if image.public_url:
            return image.public_url
        if self.settings.inline_reference_images:
            if not image.path.is_file():
                raise FileNotFoundError(image.path)
            with Image.open(image.path) as source:
                normalized = ImageOps.exif_transpose(source).convert("RGB")
                # The whole card, long side 1280.  Fitting every reference to 720x1280 cut a 16:9 scene card
                # down to its middle third - the buildings, doors and space either side went missing.
                normalized = ImageOps.contain(
                    normalized,
                    (1280, 1280),
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
                headers=self.image_headers, json=payload, timeout=min(self.settings.request_timeout, SUBMIT_TIMEOUT_SECONDS),
            )
            response.raise_for_status()
            return response

        task_path = output.with_suffix(output.suffix + ".task.json")
        record = json.loads(task_path.read_text(encoding="utf-8")) if task_path.exists() else {}
        if unconfirmed(record):
            raise SubmissionUncertain(held_message(record))
        task_id = record.get("task_id")
        cached = bool(task_id)
        if not cached:
            task_id = self._submit_recorded(submit, task_path, {"kind": "image"})
            if task_id:
                atomic_write_json(task_path, {"task_id": task_id, "kind": "image"})
        if not task_id:
            raise ValueError("image API returned no task_id")
        try:
            url = self._poll_image_url(str(task_id))
            self._download(url, output, max_bytes=64 * 1024 * 1024)
        except (RuntimeError, httpx.HTTPStatusError) as error:
            task_path.unlink(missing_ok=True)
            # A task id remembered from an earlier run may point at a result
            # file the service has since purged (403/404 on download).  That
            # card is not coming back: submit it again, once.
            if not cached or not isinstance(error, httpx.HTTPStatusError):
                raise
            task_id = self._submit_recorded(submit, task_path, {"kind": "image"})
            if not task_id:
                raise ValueError("image API returned no task_id") from error
            atomic_write_json(task_path, {"task_id": task_id, "kind": "image"})
            url = self._poll_image_url(str(task_id))
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
                json=payload, timeout=min(self.settings.request_timeout, SUBMIT_TIMEOUT_SECONDS),
            )
            response.raise_for_status()
            return response

        # A synchronous generation has no task to look up: an unconfirmed one is marked beside the output.
        marker = output.with_suffix(output.suffix + ".unconfirmed.json")
        record = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
        if unconfirmed(record):
            raise SubmissionUncertain(held_message(record))
        try:
            response = submit_once(submit)
        except SubmissionUncertain:
            atomic_write_json(marker, {"submit_uncertain_at": time.time(), "kind": "image"})
            raise
        marker.unlink(missing_ok=True)
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

    @staticmethod
    def _audio_data_url(path: Path) -> str:
        """Reference voice as a data URI; the service takes it like an inline image."""
        payload = path.read_bytes()
        if len(payload) > 10 * 1024 * 1024:
            raise ValueError(f"reference audio exceeds 10 MiB: {path}")
        return "data:audio/wav;base64," + base64.b64encode(payload).decode("ascii")

    def _video_payload(
        self,
        prompt: str,
        image_url: str | None,
        duration: float,
        additional_image_urls: tuple[str, ...] = (),
        reference_audio_urls: tuple[str, ...] = (),
    ) -> dict:
        limits = VIDEO_MODEL_LIMITS.get(self.settings.video_model, {})
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
        # Voice references: the model clones each speaker's timbre from these
        # and assigns them to the on-screen speakers itself (the @音频N text
        # binding is not honoured, see docs/seedance-reference-audio.md).
        content.extend(
            {
                "type": "audio_url",
                "audio_url": {"url": audio_url},
                "role": "reference_audio",
            }
            for audio_url in reference_audio_urls
        )
        return {
            "model": self.settings.video_model,
            "content": content,
            "ratio": "9:16",
            "resolution": limits.get("resolution", "720p"),
            "duration": max(4, min(int(limits.get("max_duration", 30)), math.ceil(duration))),
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
        reference_audios: tuple[Path, ...] = (),
    ) -> Path:
        for additional_image in additional_images:
            if not additional_image.is_file():
                raise FileNotFoundError(additional_image)
        for reference_audio in reference_audios:
            if not reference_audio.is_file():
                raise FileNotFoundError(reference_audio)
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
            tuple(self._audio_data_url(path) for path in reference_audios),
        )
        request_sha256 = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

        def submit() -> httpx.Response:
            response = self.client.post(
                f"{self.settings.phanrouter_base_url.rstrip('/')}/api/v3/contents/generations/tasks",
                headers=self.video_headers, json=payload, timeout=min(self.settings.request_timeout, SUBMIT_TIMEOUT_SECONDS),
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
        task_id = None
        if task_path.exists():
            cached = json.loads(task_path.read_text(encoding="utf-8"))
            if cached.get("request_sha256") == request_sha256:
                task_id = cached.get("task_id")
                if unconfirmed(cached):
                    # Waiting a while and sending it again still made a second task whenever the first had been
                    # accepted.  With no way to ask the service, only someone who has seen the bill may resend it.
                    raise SubmissionUncertain(held_message(cached))
            else:
                # The request changed since that task was created (re-plan,
                # sanitised line, send-time alias): the old task's video would
                # not be this clip.  Keep the record aside and submit afresh.
                task_path.replace(task_path.with_suffix(".stale.json"))
        if not task_id:
            task_id = self._submit_recorded(submit, task_path, {"request_sha256": request_sha256, "kind": "video",
                                                                "model": self.settings.video_model})
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
                        "reference_audio_sha256s": [
                            hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in reference_audios
                        ],
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
                usage = data.get("usage") or {}
                if usage and task_path.is_file():
                    # The service bills in tokens, not seconds; keep the figure
                    # beside the task id so the cost ledger can use it.
                    try:
                        record = json.loads(task_path.read_text(encoding="utf-8"))
                        record["usage"] = usage
                        atomic_write_json(task_path, record)
                    except (OSError, ValueError):
                        pass
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
