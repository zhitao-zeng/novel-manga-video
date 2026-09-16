"""Video payload rules, recorded-task resumption, polling and usage accounting."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import time
from pathlib import Path
import httpx
from ..util import atomic_write_json
from .downloads import download_file
from .phanrouter_tasks import SUBMIT_TIMEOUT_SECONDS, SubmissionUncertain, unconfirmed, held_message, submit_recorded, task_data

VIDEO_MODEL_LIMITS: dict[str, dict[str, object]] = {
    "MiniMax-H3": {"resolution": "768P", "max_duration": 15},
}


def video_payload(
    settings,
    prompt: str,
    image_url: str | None,
    duration: float,
    additional_image_urls: tuple[str, ...] = (),
    reference_audio_urls: tuple[str, ...] = (),
) -> dict:
    limits = VIDEO_MODEL_LIMITS.get(settings.video_model, {})
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
        "model": settings.video_model,
        "content": content,
        "ratio": "9:16",
        "resolution": limits.get("resolution", "720p"),
        "duration": max(4, min(int(limits.get("max_duration", 30)), math.ceil(duration))),
        "generate_audio": True,
        "watermark": False,
        "output_format": "mp4",
    }


def generate_video(settings, client, video_headers, payload, output, additional_images=(), reference_audios=()):
    request_sha256 = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    def submit() -> httpx.Response:
        response = client.post(
            f"{settings.phanrouter_base_url.rstrip('/')}/api/v3/contents/generations/tasks",
            headers=video_headers, json=payload, timeout=min(settings.request_timeout, SUBMIT_TIMEOUT_SECONDS),
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
        task_id = submit_recorded(submit, task_path, {"request_sha256": request_sha256, "kind": "video",
                                                            "model": settings.video_model})
        if task_id:
            atomic_write_json(
                task_path,
                {
                    "task_id": task_id,
                    "kind": "video",
                    "model": settings.video_model,
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
    deadline = time.monotonic() + settings.poll_timeout
    while time.monotonic() < deadline:
        response = client.get(
            f"{settings.phanrouter_base_url.rstrip('/')}/api/v3/contents/generations/tasks/{task_id}",
            headers=video_headers,
        )
        response.raise_for_status()
        data = task_data(response.json())
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
                download_file(client, str(url), output)
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




def audio_data_url(path: Path) -> str:
    """Reference voice as a data URI; the service takes it like an inline image."""
    payload = path.read_bytes()
    if len(payload) > 10 * 1024 * 1024:
        raise ValueError(f"reference audio exceeds 10 MiB: {path}")
    return "data:audio/wav;base64," + base64.b64encode(payload).decode("ascii")

