"""Video from the MiniMax-H3 service on the internal network.

The service speaks a small async job API of its own rather than PhanRouter's tasks endpoint:
POST /v1/videos returns an id, GET /v1/videos/{id} reports status, and /content hands back the
MP4.  The client sends the output size and sampling parameters explicitly with each request.

Only the video call moves here.  Character and location cards still go through PhanRouter,
because that is where the image model lives - so this class inherits everything else.

Two limits shape how it is used: a clip may run 4-15 s (a 30 s range cannot use it at all), and
each instance renders one job at a time, queueing the rest.  NOVEL_LOCAL_H3_URL names either one
instance or ``pool`` - the resident instances and the night shift's leased ones together (see
h3_pool.py), where each clip goes to whichever instance has room when it is submitted.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from ..config import Settings
from ..util import atomic_write_json, read_json
from .base import ImageResult
from .h3_pool import H3Pool
from .h3_pool import release as release_slot
from .phanrouter import PhanRouterMediaProvider
from .phanrouter_video import audio_data_url
from .phanrouter_tasks import SubmissionUncertain, submit_once

# The planner writes @图片N and @音频N; H3 addresses its inputs as <Picture N> and <Audio N>.
# Both are positional and the caller hands over the references in the order the plan numbered
# them, so this is a straight renaming - the plan, the request record and the subtitles keep
# the tags the rest of the pipeline matches on.
PICTURE_TAG = re.compile(r"@图片(\d+)")
AUDIO_TAG = re.compile(r"@音频(\d+)")
# The planner writes every spoken line as 说：{台词}; H3 delimits speech as <d>[Language] ...</d>.
# Off by default: it does make H3 say every line (missing 0.080 -> 0.000) but it then invents
# even more on top, and CER goes 0.78 -> 1.04.  Kept behind a flag as a component of a fix,
# not a fix.
SPOKEN_LINE = re.compile(r"说：\{([^}]*)\}")
NOTHING_ELSE = (
    "\n【只说这些】本段的全部人声就是上面 <d> 标记里的台词，逐字说完即可；"
    "除此之外不得再说任何话，不得即兴发挥、不得重复台词、不得添加旁白或语气词。"
    "没有台词的时间里保持安静，只有环境声和动作音效。"
)
SHORT_EDGE = 544  # output size; the service retains its 8step_v1.0_768p LoRA
MIN_SECONDS, MAX_SECONDS = 4, 15
POLL_SECONDS = 3.0


def h3_prompt(prompt: str) -> str:
    prompt = AUDIO_TAG.sub(r"<Audio \1>", PICTURE_TAG.sub(r"<Picture \1>", prompt))
    if os.environ.get("NOVEL_H3_MARK_DIALOGUE", "0") != "1":
        return prompt
    marked, count = SPOKEN_LINE.subn(r"说：<d>[Chinese] \1</d>", prompt)
    # Only worth saying when there is dialogue: a wordless clip has no lines to be the whole of.
    return marked + NOTHING_ELSE if count else marked


class InstanceUnavailable(RuntimeError):
    """No connection was made for a submission, or a task lookup is unavailable."""


def task_downloaded(record: dict) -> bool:
    return any((record.get(key) or {}).get('completed_observed_at')
               for key in ('client_timing', 'resume_timing'))


def guard_pending_tasks(directory: Path, output: Path) -> None:
    """Changing the attempt directory cannot abandon this clip's unresolved task."""
    for path in directory.glob('attempt_*/clip.mp4.task.json'):
        record = read_json(path, {})
        if record.get('submit_uncertain_at') and not record.get('task_id'):
            raise SubmissionUncertain(f'H3 submission unconfirmed at {path}; reconcile it before another submission')
        video = path.with_suffix('').with_suffix('')
        if (record.get('task_id') and video != output
                and (not (video.is_file() and video.stat().st_size > 0)
                     or (record.get('client_timing') and not task_downloaded(record)))):
            raise SubmissionUncertain(f'H3 task {record["task_id"]} is unresolved at {path}; resume its original attempt')


class LocalH3MediaProvider(PhanRouterMediaProvider):
    def __init__(self, settings: Settings, base_url: str, ratio: str = "16:9") -> None:
        super().__init__(settings)
        target = base_url.strip()
        # "pool" (or "pool:<config path>") draws an instance for every clip; anything else is one.
        self.pool = H3Pool(target[5:] or None) if target == "pool" or target.startswith("pool:") else None
        self.base_url = "" if self.pool else target.rstrip("/")
        self.ratio = ratio
        # Internal generation and artifact traffic must not use the session's
        # public-network proxy. The inherited image provider keeps its client.
        self.h3_client = httpx.Client(timeout=settings.request_timeout, trust_env=False)

    # ---------------------------------------------------------------- inputs
    @staticmethod
    def _image_data_url(path: Path, max_side: int = 1280) -> str:
        """A card as an inline JPEG.  The service is not on a shared filesystem with us, so
        every reference travels in the request body; sending a four-megabyte turnaround would
        spend seconds uploading detail that a 544-line frame throws away."""
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            if max(image.size) > max_side:
                scale = max_side / max(image.size)
                image = image.resize((round(image.width * scale), round(image.height * scale)))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=88)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    def sampling_parameters(self) -> dict:
        """The sampling configuration actually sent by this local H3 lane."""
        return {"short_edge": SHORT_EDGE, "aspect_ratio": self.ratio,
                "num_inference_steps": 9, "flow_shift": 12.0, "audio_flow_shift": 3.0}

    def _payload(self, prompt: str, images: list[Path], audios: tuple[Path, ...], duration: float) -> dict:
        sampling = self.sampling_parameters()
        seconds = max(MIN_SECONDS, min(MAX_SECONDS, math.ceil(duration)))
        conditions = [{"type": "image", "role": "reference", "uri": self._image_data_url(path)}
                      for path in images]
        conditions += [{"type": "audio", "role": "reference", "uri": audio_data_url(path)}
                       for path in audios]
        return {
            "model": self.settings.video_model,
            "task": "ref2va",
            "prompt": h3_prompt(prompt),
            "seconds": seconds,
            "conditions": conditions,
            "target": {"short_edge": sampling["short_edge"], "aspect_ratio": sampling["aspect_ratio"],
                       "duration_seconds": float(seconds)},
            "num_outputs_per_prompt": 1,
            # This service counts sigma points, including the terminal point:
            # nine points give the eight evaluations of the 8step_v1.0_768p LoRA.
            "num_inference_steps": sampling["num_inference_steps"],
            "flow_shift": sampling["flow_shift"],
            "audio_flow_shift": sampling["audio_flow_shift"],
        }

    @staticmethod
    def _request_digest(payload: dict, seed_variant: int) -> str:
        # Keep the existing request/seed formula for both fresh and resumed tasks.
        return hashlib.sha256(json.dumps(
            {**payload, **({'seed_variant': seed_variant} if seed_variant else {})},
            ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------- API
    def _submit(self, payload: dict, base: str) -> str:
        def post() -> httpx.Response:
            response = self.h3_client.post(f"{base}/v1/videos", json=payload,
                                          timeout=min(self.settings.request_timeout, 300.0))
            if response.status_code >= 500:
                # H3 has no contract proving a 5xx (including 503) rejected the
                # request before accepting it. Do not inherit paid API assumptions.
                raise SubmissionUncertain(f'H3 submission unconfirmed (HTTP {response.status_code}); not sent again')
            response.raise_for_status()
            return response

        try:
            response = submit_once(post, attempts=1 if self.pool else 3)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as error:
            raise InstanceUnavailable(f'local H3 at {base} not connected: {type(error).__name__}') from error
        try:
            task_id = response.json().get('id')
        except (ValueError, AttributeError) as error:
            raise SubmissionUncertain('H3 submission returned no readable task id; not sent again') from error
        if not task_id:
            raise SubmissionUncertain('H3 submission returned no task id; not sent again')
        return str(task_id)

    def _state(self, task_id: str, base: str) -> dict | None:
        """The task's state, or None if the service has forgotten it."""
        response = self.h3_client.get(f"{base}/v1/videos/{task_id}")
        if response.status_code == 404:
            return None
        if response.status_code >= 500:
            raise InstanceUnavailable(f"local H3 at {base} returned HTTP {response.status_code} while polling")
        response.raise_for_status()
        return response.json()

    def create_video(
        self,
        prompt: str,
        image: ImageResult | None,
        output: Path,
        duration: float,
        additional_images: tuple[Path, ...] = (),
        reference_audios: tuple[Path, ...] = (),
        seed_variant: int = 0,
    ) -> Path:
        call_started = time.monotonic()
        timing = {'call_started_at': time.time(), 'pool_wait_s': 0.0, 'submission_s': 0.0}
        images = ([Path(image.path)] if image is not None else []) + [Path(p) for p in additional_images]
        for path in (*images, *reference_audios):
            if not Path(path).is_file():
                raise FileNotFoundError(path)
        payload = self._payload(prompt, images, tuple(reference_audios), duration)
        sampling = self.sampling_parameters()
        request_sha256 = self._request_digest(payload, seed_variant)

        task_path = output.with_suffix(output.suffix + ".task.json")
        task_id, base = None, self.base_url
        resumed_sampling = None
        if task_path.exists():
            try:
                cached = json.loads(task_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = {}
            if cached.get('submit_uncertain_at') and not cached.get('task_id'):
                raise SubmissionUncertain(f'H3 submission unconfirmed at {task_path}; reconcile it before another submission')
            matches = cached.get("request_sha256") == request_sha256
            if matches:
                resumed_sampling = sampling
            elif cached and sampling["short_edge"] == 544:
                previous_sampling = {**sampling, "short_edge": 768}
                if cached.get("h3_sampling") in (None, previous_sampling):
                    previous_payload = {**payload, "target": {**payload["target"], "short_edge": 768}}
                    if cached.get("request_sha256") == self._request_digest(previous_payload, seed_variant):
                        # Only the default output size changed. Finish the submitted
                        # task using its original receipt rather than paying for another.
                        matches = True
                        resumed_sampling = previous_sampling
            if matches:
                task_id = cached.get("task_id")
                # The task lives on the instance that took it, whichever that was.
                base = str(cached.get("endpoint") or "").removesuffix("/v1/videos") or self.base_url
            elif cached:
                if cached.get('task_id') and not task_downloaded(cached):
                    raise SubmissionUncertain('H3 request changed while its original task is unresolved; resume the original inputs')
                task_path.replace(task_path.with_suffix(".stale.json"))
        if self.pool and task_id and (not base or self.pool.excluded(base)):
            raise SubmissionUncertain('H3 task belongs to an unavailable or excluded instance; original receipt retained')
        elif task_id and not self.pool and base != self.base_url:
            raise SubmissionUncertain('H3 task belongs to a different instance; original receipt retained')

        if task_id and cached.get("h3_sampling") != resumed_sampling:
            # The digest establishes these exact parameters even for a legacy
            # receipt. Retain its original task id, seed and request SHA.
            atomic_write_json(task_path, {**cached, "h3_sampling": resumed_sampling})
        # This remains the NEW-request seed. An old task keeps its existing id.
        payload["seed"] = int(request_sha256[:8], 16) % (2 ** 31)

        slot, instance, status = None, None, "pending"
        submitted = time.monotonic()
        timing['resumed'] = bool(task_id)

        def lookup():
            try:
                state = self._state(task_id, base)
            except (httpx.HTTPError, InstanceUnavailable, ValueError) as error:
                if self.pool:
                    self.pool.cool_down(base, self.pool.unreachable_cooldown, f'lookup failed: {type(error).__name__}')
                raise SubmissionUncertain(f'H3 task {task_id} could not be queried; original receipt retained') from error
            if state is None:
                raise SubmissionUncertain(f'H3 task {task_id} is missing from the service index; reconcile its outcome before resubmitting')
            return state

        def download_completed(state):
            nonlocal slot
            # A completed GPU task needs no instance slot during its download.
            # Release it before network/disk transfer so the next clip can submit.
            release_slot(slot)
            slot = None
            ready = time.monotonic()
            timing['service_observed_s'] = round(ready-submitted, 3)
            timing['completed_observed_at'] = time.time()
            self._download(f"{base}/v1/videos/{task_id}/content", output, client=self.h3_client)
            timing['download_s'] = round(time.monotonic()-ready, 3)
            timing['provider_elapsed_s'] = round(time.monotonic()-call_started, 3)
            receipt = read_json(task_path, {})
            field = 'resume_timing' if timing['resumed'] and receipt.get('client_timing') else 'client_timing'
            atomic_write_json(task_path, {**receipt, field: timing,
                'service_timing': {key: state[key] for key in
                    ('created_at', 'completed_at', 'inference_time_s') if key in state}})
            return output

        try:
            deadline = time.monotonic() + self.settings.poll_timeout
            if self.pool and task_id:
                # Completed tasks need no slot. A failed lookup never grants a new POST.
                state = lookup()
                status = str(state.get("status", "")).lower()
                if status in {"completed", "succeeded", "success"}:
                    return download_completed(state)
                elif status not in {"failed", "error", "cancelled", "canceled"}:
                    from novel_manga.resource_waits import QueueWait
                    waiting = time.monotonic()
                    with QueueWait('h3_instance') as queue:
                        queue.begin()
                        slot = self.pool.hold(base, timeout=None)
                    waited=time.monotonic()-waiting
                    deadline+=waited
                    timing['pool_wait_s'] += round(waited, 3)
            while time.monotonic() < deadline:
                if not task_id:
                    release_slot(slot)
                    slot = None
                    before_submit = getattr(self, 'before_video_submit', None)
                    if before_submit is not None:
                        probing = time.monotonic()
                        before_submit()
                        # No main task has been submitted; its polling allowance
                        # starts after the probe, which acquires its own pool slot.
                        deadline += time.monotonic()-probing
                    if self.pool:
                        # Unavailable capacity is queue time. Only a submitted
                        # generation consumes the task's polling allowance.
                        from novel_manga.resource_waits import QueueWait
                        waiting = time.monotonic()
                        with QueueWait('h3_instance') as queue:
                            queue.begin()
                            instance, slot = self.pool.acquire(timeout=None)
                        waited=time.monotonic()-waiting
                        deadline+=waited
                        timing['pool_wait_s'] += round(waited, 3)
                        base = instance.url
                    sending = time.monotonic()
                    record = {
                        'kind': 'video', 'model': self.settings.video_model,
                        'endpoint': f'{base}/v1/videos',
                        **({'instance': instance.name} if instance else {}),
                        'local': True, 'output_format': 'mp4', 'request_sha256': request_sha256,
                        'seconds': payload['seconds'], 'seed': payload['seed'], 'h3_sampling': sampling,
                        'additional_image_sha256s': [hashlib.sha256(Path(p).read_bytes()).hexdigest()
                                                    for p in additional_images],
                        'reference_audio_sha256s': [hashlib.sha256(Path(p).read_bytes()).hexdigest()
                                                   for p in reference_audios],
                    }
                    # A crash after sending but before receiving an id must leave
                    # the same durable hold as a lost HTTP response.
                    atomic_write_json(task_path, {**record, 'submit_uncertain_at': time.time()})
                    try:
                        task_id = self._submit(payload, base)
                    except InstanceUnavailable as error:
                        atomic_write_json(task_path, {**record, 'submit_not_accepted_at': time.time()})
                        if not self.pool:
                            raise
                        self.pool.cool_down(base, self.pool.unreachable_cooldown, str(error))
                        continue
                    except SubmissionUncertain:
                        raise
                    except RuntimeError:
                        # Explicit rejection (e.g. 4xx) occurred before acceptance.
                        atomic_write_json(task_path, {**record, 'submit_not_accepted_at': time.time()})
                        raise
                    finally:
                        timing['submission_s'] += round(time.monotonic()-sending, 3)
                    submitted = time.monotonic()
                    timing.update(resumed=False, accepted_at=time.time())
                    atomic_write_json(task_path, {**record, 'task_id': task_id, 'client_timing': timing})
                state = lookup()
                status = str(state.get("status", "")).lower()
                if status in {"completed", "succeeded", "success"}:
                    return download_completed(state)
                if status in {"failed", "error", "cancelled", "canceled"}:
                    detail = json.dumps(state.get("error") or state, ensure_ascii=False)[:400]
                    # A failed task is history, not one to resume: set its record aside, or every later call
                    # reads the same failure back instead of asking again.
                    if task_path.exists():
                        task_path.replace(task_path.with_suffix(".failed.json"))
                    raise RuntimeError(f"local H3 task {task_id} {status}: {detail}")
                if self.pool and self.pool.problem(base):
                    raise SubmissionUncertain(f'H3 instance unhealthy with task {task_id} pending; original receipt retained')
                if self.pool and time.monotonic() - submitted > self.pool.stuck_seconds:
                    # An instance that takes work and never finishes it (GPU003-B, 2026-09-11 14:36).
                    self.pool.cool_down(base, self.pool.stuck_cooldown,
                                        f"task {task_id} still {status} after {self.pool.stuck_seconds / 60:.0f} min")
                    raise SubmissionUncertain(f'H3 task {task_id} still pending; original receipt retained')
                time.sleep(POLL_SECONDS)
            raise TimeoutError(f"local H3 task {task_id} still {status} after {self.settings.poll_timeout:.0f}s")
        finally:
            release_slot(slot)
