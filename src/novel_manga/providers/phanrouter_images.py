"""Image task execution and resumption; reference upload and video tasks are separate."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from pathlib import Path
import httpx
from PIL import Image, ImageOps
from ..util import atomic_write_json
from .base import ImageResult, image_dimensions
from .downloads import download_file
from .phanrouter_tasks import (SUBMIT_TIMEOUT_SECONDS, PURGED_STATUSES, SubmissionUncertain,
                              unconfirmed, held_message, submit_once, submit_recorded, task_data)

def poll_image_url(settings, client, image_headers, task_id: str) -> str:
    deadline = time.monotonic() + settings.poll_timeout
    while time.monotonic() < deadline:
        response = client.get(
            f"{settings.phanrouter_base_url.rstrip('/')}/v3/images/generations/{task_id}",
            headers=image_headers,
        )
        response.raise_for_status()
        data = task_data(response.json())
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


def create_image(
    settings, client, image_headers,
    prompt: str,
    output: Path,
    reference: Path | None = None,
    additional_references: tuple[Path, ...] = (),
    *, aspect_ratio: str = "9:16",
) -> ImageResult:
    if settings.image_model in {
        "doubao-seedream-5.0-lite",
        "doubao-seedream-4.5",
    }:
        if additional_references:
            raise ValueError("Seedream image generation accepts only one reference")
        return create_seedream_image(settings, client, image_headers, prompt, output, reference, aspect_ratio=aspect_ratio)

    payload: dict[str, object] = {
        "model": settings.image_model,
        "prompt": prompt,
        "aspectRatio": aspect_ratio,
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
        response = client.post(
            f"{settings.phanrouter_base_url.rstrip('/')}/v3/images/generations",
            headers=image_headers, json=payload, timeout=min(settings.request_timeout, SUBMIT_TIMEOUT_SECONDS),
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
        task_id = submit_recorded(submit, task_path, {"kind": "image"})
        if task_id:
            atomic_write_json(task_path, {"task_id": task_id, "kind": "image"})
    if not task_id:
        raise ValueError("image API returned no task_id")
    try:
        url = poll_image_url(settings, client, image_headers, str(task_id))
        download_file(client, url, output, max_bytes=64 * 1024 * 1024)
    except (RuntimeError, httpx.HTTPStatusError) as error:
        purged = isinstance(error, httpx.HTTPStatusError) and error.response.status_code in PURGED_STATUSES
        if isinstance(error, httpx.HTTPStatusError) and not purged:
            # A hiccup while polling or downloading (a 5xx, a 429): the task stands, and a later run picks it
            # up again.  Dropping its record here had the next build pay for a second image of the same card.
            raise
        task_path.unlink(missing_ok=True)
        # A task id remembered from an earlier run may point at a result
        # file the service has since purged (403/404 on download).  That
        # card is not coming back: submit it again, once.
        if not cached or not purged:
            raise
        task_id = submit_recorded(submit, task_path, {"kind": "image"})
        if not task_id:
            raise ValueError("image API returned no task_id") from error
        atomic_write_json(task_path, {"task_id": task_id, "kind": "image"})
        url = poll_image_url(settings, client, image_headers, str(task_id))
        download_file(client, url, output, max_bytes=64 * 1024 * 1024)
    return ImageResult(path=output, public_url=url)


def create_seedream_image(
    settings, client, image_headers,
    prompt: str,
    output: Path,
    reference: Path | None = None,
    *, aspect_ratio: str = "9:16",
) -> ImageResult:
    payload: dict[str, object] = {
        "model": settings.image_model,
        "prompt": prompt,
        "n": 1,
        "size": "x".join(map(str, image_dimensions(aspect_ratio))),
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
        response = client.post(
            f"{settings.phanrouter_base_url.rstrip('/')}/v1/images/generations",
            headers=image_headers,
            json=payload, timeout=min(settings.request_timeout, SUBMIT_TIMEOUT_SECONDS),
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
    download_file(client, str(url), output, max_bytes=64 * 1024 * 1024)
    atomic_write_json(
        output.with_suffix(output.suffix + ".task.json"),
        {
            "kind": "image",
            "model": settings.image_model,
            "endpoint": "/v1/images/generations",
            "request_sha256": hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        },
    )
    return ImageResult(path=output, public_url=str(url))


