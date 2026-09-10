"""Video from the MiniMax-H3 service on the internal network.

The service (GPU052, two instances) speaks a small async job API of its own rather than
PhanRouter's tasks endpoint: POST /v1/videos returns an id, GET /v1/videos/{id} reports
status, and /content hands back the MP4.  It costs nothing, renders 960x544 at 24 fps with
sound, and takes about two thirds of a second of wall clock per second of finished video.

Only the video call moves here.  Character and location cards still go through PhanRouter,
because that is where the image model lives - so this class inherits everything else.

Two limits shape how a lane is configured for it: a clip may run 4-15 s (a 30 s range cannot
use it at all), and each instance renders one job at a time, queueing the rest.  So a key
pointed at an instance carries one or two in-flight slots, not the dozens a paid key does.
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
from .phanrouter import PhanRouterMediaProvider

# The planner writes @图片N and @音频N; H3 addresses its inputs as <Picture N> and <Audio N>.
# Both are positional and the caller hands over the references in the order the plan numbered
# them, so this is a straight renaming - the plan, the request record and the subtitles keep
# the tags the rest of the pipeline matches on.
PICTURE_TAG = re.compile(r"@图片(\d+)")
AUDIO_TAG = re.compile(r"@音频(\d+)")
# The planner writes every spoken line as 说：{台词}; H3 delimits speech as <d>[Language] ...</d>.
SPOKEN_LINE = re.compile(r"说：\{([^}]*)\}")
NOTHING_ELSE = (
    "\n【只说这些】本段的全部人声就是上面 <d> 标记里的台词，逐字说完即可；"
    "除此之外不得再说任何话，不得即兴发挥、不得重复台词、不得添加旁白或语气词。"
    "没有台词的时间里保持安静，只有环境声和动作音效。"
)
SHORT_EDGE = 544  # what the deployed four-step Ref2VA turbo build renders
MIN_SECONDS, MAX_SECONDS = 4, 15
POLL_SECONDS = 3.0


def h3_prompt(prompt: str) -> str:
    prompt = AUDIO_TAG.sub(r"<Audio \1>", PICTURE_TAG.sub(r"<Picture \1>", prompt))
    if os.environ.get("NOVEL_H3_MARK_DIALOGUE", "1") != "1":
        return prompt
    marked, count = SPOKEN_LINE.subn(r"说：<d>[Chinese] \1</d>", prompt)
    # Only worth saying when there is dialogue: a wordless clip has no lines to be the whole of.
    return marked + NOTHING_ELSE if count else marked


class LocalH3MediaProvider(PhanRouterMediaProvider):
    def __init__(self, settings: Settings, base_url: str, ratio: str = "16:9") -> None:
        super().__init__(settings)
        self.base_url = base_url.rstrip("/")
        self.ratio = ratio

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
        conditions += [{"type": "audio", "role": "reference", "uri": self._audio_data_url(path)}
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
            "num_inference_steps": 5,
            "flow_shift": 12.0,
            "audio_flow_shift": 3.0,
        }

    # ------------------------------------------------------------------- API
    def _submit(self, payload: dict) -> str:
        def post() -> httpx.Response:
            response = self.client.post(f"{self.base_url}/v1/videos", json=payload,
                                        timeout=min(self.settings.request_timeout, 300.0))
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                detail = response.text.strip().replace("\n", " ")[:1000]
                raise RuntimeError(f"local H3 submission returned HTTP {response.status_code}: {detail}") from error
            return response

        task_id = retry(post).json().get("id")
        if not task_id:
            raise ValueError("local H3 returned no task id")
        return str(task_id)

    def _state(self, task_id: str) -> dict | None:
        """The task's state, or None if the service has forgotten it."""
        response = self.client.get(f"{self.base_url}/v1/videos/{task_id}")
        if response.status_code == 404:
            return None
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
    ) -> Path:
        images = ([Path(image.path)] if image is not None else []) + [Path(p) for p in additional_images]
        for path in (*images, *reference_audios):
            if not Path(path).is_file():
                raise FileNotFoundError(path)
        payload = self._payload(prompt, images, tuple(reference_audios), duration)
        request_sha256 = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        # A seed drawn from the request keeps a resubmission of the same clip identical, while
        # a repaired clip - whose prompt changed - gets a different draw of the dice.
        payload["seed"] = int(request_sha256[:8], 16) % (2 ** 31)

        task_path = output.with_suffix(output.suffix + ".task.json")
        task_id = None
        if task_path.exists():
            try:
                cached = json.loads(task_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = {}
            if cached.get("request_sha256") == request_sha256:
                task_id = cached.get("task_id")
            elif cached:
                task_path.replace(task_path.with_suffix(".stale.json"))

        deadline = time.monotonic() + self.settings.poll_timeout
        while time.monotonic() < deadline:
            if not task_id:
                task_id = self._submit(payload)
                atomic_write_json(task_path, {
                    "task_id": task_id,
                    "kind": "video",
                    "model": self.settings.video_model,
                    "endpoint": f"{self.base_url}/v1/videos",
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
            state = self._state(task_id)
            if state is None:
                # The service keeps its task index in memory, so a restart loses every id we
                # hold.  Nothing was charged and nothing is running: ask for the work again.
                task_id = None
                continue
            status = str(state.get("status", "")).lower()
            if status in {"completed", "succeeded", "success"}:
                self._download(f"{self.base_url}/v1/videos/{task_id}/content", output)
                return output
            if status in {"failed", "error", "cancelled", "canceled"}:
                detail = json.dumps(state.get("error") or state, ensure_ascii=False)[:400]
                raise RuntimeError(f"local H3 task {task_id} {status}: {detail}")
            time.sleep(POLL_SECONDS)
        raise TimeoutError(f"local H3 task {task_id} still {status if 'status' in dir() else 'pending'} "
                           f"after {self.settings.poll_timeout:.0f}s")
