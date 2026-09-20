"""Video from the MiniMax-H3 service on the internal network.

The service speaks a small async job API of its own rather than PhanRouter's tasks endpoint:
POST /v1/videos returns an id, GET /v1/videos/{id} reports status, and /content hands back the
MP4.  It costs nothing, renders 960x544 at 24 fps with sound, and takes about two thirds of a
second of wall clock per second of finished video.

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
from ..util import atomic_write_json, retry
from .base import ImageResult
from .h3_pool import H3Pool
from .h3_pool import release as release_slot
from .phanrouter import PhanRouterMediaProvider
from .phanrouter_video import audio_data_url

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
SHORT_EDGE = 768  # the deployed Ref2VA turbo LoRA is the 768p build; it renders native 1344x768
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
    """The instance failed (refused, dropped or answered 5xx), not the request: try another."""


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

    def _payload(self, prompt: str, images: list[Path], audios: tuple[Path, ...], duration: float) -> dict:
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
            "target": {"short_edge": SHORT_EDGE, "aspect_ratio": self.ratio,
                       "duration_seconds": float(seconds)},
            "num_outputs_per_prompt": 1,
            # Matched to the deployed 8step_v1.0_768p LoRA.  The previous 5 matched nothing:
            # the build behind it was the four-step one.
            "num_inference_steps": 8,
            "flow_shift": 12.0,
            "audio_flow_shift": 3.0,
        }

    # ------------------------------------------------------------------- API
    def _submit(self, payload: dict, base: str) -> str:
        def post() -> httpx.Response:
            try:
                response = self.h3_client.post(f"{base}/v1/videos", json=payload,
                                            timeout=min(self.settings.request_timeout, 300.0))
            except httpx.TransportError as error:
                raise InstanceUnavailable(f"local H3 at {base} unreachable: {type(error).__name__}") from error
            if response.status_code >= 500:
                raise InstanceUnavailable(f"local H3 at {base} returned HTTP {response.status_code}")
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                detail = response.text.strip().replace("\n", " ")[:1000]
                raise RuntimeError(f"local H3 submission returned HTTP {response.status_code}: {detail}") from error
            return response

        # One instance is retried; in the pool a failing instance is cooled down and the clip moves on.
        task_id = (post() if self.pool else retry(post)).json().get("id")
        if not task_id:
            raise ValueError("local H3 returned no task id")
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
        images = ([Path(image.path)] if image is not None else []) + [Path(p) for p in additional_images]
        for path in (*images, *reference_audios):
            if not Path(path).is_file():
                raise FileNotFoundError(path)
        payload = self._payload(prompt, images, tuple(reference_audios), duration)
        request_sha256 = hashlib.sha256(
            json.dumps({**payload, **({'seed_variant': seed_variant} if seed_variant else {})}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        # A seed drawn from the request keeps a resubmission of the same clip identical, while
        # a repaired clip - whose prompt changed - gets a different draw of the dice.
        payload["seed"] = int(request_sha256[:8], 16) % (2 ** 31)

        task_path = output.with_suffix(output.suffix + ".task.json")
        task_id, base = None, self.base_url
        if task_path.exists():
            try:
                cached = json.loads(task_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = {}
            if cached.get("request_sha256") == request_sha256:
                task_id = cached.get("task_id")
                # The task lives on the instance that took it, whichever that was.
                base = str(cached.get("endpoint") or "").removesuffix("/v1/videos") or self.base_url
            elif cached:
                task_path.replace(task_path.with_suffix(".stale.json"))
        if self.pool and task_id and (not base or self.pool.excluded(base)):
            task_id = None  # held by an instance taken out of the pool: render it elsewhere

        slot, instance, status = None, None, "pending"
        submitted = time.monotonic()
        try:
            deadline = time.monotonic() + self.settings.poll_timeout
            if self.pool and task_id:
                # A resumed task is looked at before anything waits for a slot: one that finished while we were away
                # (a download cut short) needs only its video, and one its instance forgot, or that went with its
                # instance, is asked for again.  Only a task still rendering holds a slot of its instance while we
                # wait on it - or the pool would hand the instance more clips than its slots allow.
                try:
                    state = self._state(task_id, base)
                except (httpx.TransportError, InstanceUnavailable) as error:
                    self.pool.cool_down(base, self.pool.unreachable_cooldown, f"lost while resuming: {type(error).__name__}")
                    state = None
                status = str((state or {}).get("status", "")).lower()
                if state is None:
                    task_id = None
                elif status in {"completed", "succeeded", "success"}:
                    self._download(f"{base}/v1/videos/{task_id}/content", output, client=self.h3_client)
                    return output
                elif status not in {"failed", "error", "cancelled", "canceled"}:
                    slot = self.pool.hold(base, timeout=max(1.0, deadline - time.monotonic()))
            while time.monotonic() < deadline:
                if not task_id:
                    if self.pool:
                        release_slot(slot)
                        slot = None
                        # Bounded by the clip's own time: an unbounded wait held this thread, and the lane's
                        # in-flight slot with it, for as long as no instance had room - forever, if none came.
                        instance, slot = self.pool.acquire(timeout=max(1.0, deadline - time.monotonic()))
                        base = instance.url
                    try:
                        task_id = self._submit(payload, base)
                    except InstanceUnavailable as error:
                        if not self.pool:
                            raise
                        self.pool.cool_down(base, self.pool.unreachable_cooldown, str(error))
                        continue
                    submitted = time.monotonic()
                    atomic_write_json(task_path, {
                        "task_id": task_id,
                        "kind": "video",
                        "model": self.settings.video_model,
                        "endpoint": f"{base}/v1/videos",
                        **({"instance": instance.name} if instance else {}),
                        "local": True,  # nothing was billed for this clip
                        "output_format": "mp4",
                        "request_sha256": request_sha256,
                        "seconds": payload["seconds"],
                        "seed": payload["seed"],
                        "additional_image_sha256s": [hashlib.sha256(Path(p).read_bytes()).hexdigest()
                                                     for p in additional_images],
                        "reference_audio_sha256s": [hashlib.sha256(Path(p).read_bytes()).hexdigest()
                                                    for p in reference_audios],
                    })
                try:
                    state = self._state(task_id, base)
                except (httpx.TransportError, InstanceUnavailable) as error:
                    if not self.pool:
                        raise
                    # The instance went away mid-clip - a night lease handed back, a restart: cool it
                    # down for every runner and give the clip to another instance.
                    self.pool.cool_down(base, self.pool.unreachable_cooldown, f"lost while polling: {type(error).__name__}")
                    task_id = None
                    continue
                if state is None:
                    # The service keeps its task index in memory, so a restart loses every id we
                    # hold.  Nothing was charged and nothing is running: ask for the work again.
                    task_id = None
                    continue
                status = str(state.get("status", "")).lower()
                if status in {"completed", "succeeded", "success"}:
                    self._download(f"{base}/v1/videos/{task_id}/content", output, client=self.h3_client)
                    return output
                if status in {"failed", "error", "cancelled", "canceled"}:
                    detail = json.dumps(state.get("error") or state, ensure_ascii=False)[:400]
                    # A failed task is history, not one to resume: set its record aside, or every later call
                    # reads the same failure back instead of asking again.
                    if task_path.exists():
                        task_path.replace(task_path.with_suffix(".failed.json"))
                    raise RuntimeError(f"local H3 task {task_id} {status}: {detail}")
                if self.pool and self.pool.problem(base):
                    # It stopped answering, its service holds no GPU, or its GPUs sat idle with jobs
                    # waiting (the pool has already cooled it down): the clip goes elsewhere now.
                    task_id = None
                    continue
                if self.pool and time.monotonic() - submitted > self.pool.stuck_seconds:
                    # An instance that takes work and never finishes it (GPU003-B, 2026-09-11 14:36).
                    self.pool.cool_down(base, self.pool.stuck_cooldown,
                                        f"task {task_id} still {status} after {self.pool.stuck_seconds / 60:.0f} min")
                    task_id = None
                    continue
                time.sleep(POLL_SECONDS)
            raise TimeoutError(f"local H3 task {task_id} still {status} after {self.settings.poll_timeout:.0f}s")
        finally:
            release_slot(slot)
