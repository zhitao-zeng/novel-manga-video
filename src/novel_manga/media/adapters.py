from __future__ import annotations
from pathlib import Path
import threading
from ..config import Settings
from ..providers.phanrouter import PhanRouterMediaProvider
from ..providers.phanrouter_video import VIDEO_MODEL_LIMITS
from ..providers.local_h3 import LocalH3MediaProvider

class FramedPhanRouter(PhanRouterMediaProvider):
    """PhanRouter provider whose video ratio and location-card aspect follow the frame."""

    def __init__(self, settings: Settings, frame: dict, resolution: str = "720p"):
        super().__init__(settings)
        self.frame = frame
        self.resolution = resolution
        self.prompt_aliases: dict[str, str] = {}  # profile.json "prompt_aliases": spelling sent to the model only
        self._tls = threading.local()
        original_post = self.client.post

        def post(url, *a, **kw):
            # Installed once for the shared HTTP client.  Only an image request
            # issued by this thread's create_image carries an aspect override;
            # video submissions from other threads pass through untouched.
            ratio = getattr(self._tls, "ratio", None)
            body = kw.get("json")
            if ratio and isinstance(body, dict) and "aspectRatio" in body:
                kw = {**kw, "json": {**body, "aspectRatio": ratio}}
            return original_post(url, *a, **kw)

        self.client.post = post

    def create_video(self, prompt, image, output, duration, additional_images=(), reference_audios=()):
        # A name the platform's text filter refuses (e.g. one shared with a
        # politician) is respelled in the text sent and nowhere else: the plan,
        # the request record and the subtitles keep the book's spelling, so
        # reuse matching and captions do not change.
        for old, new in self.prompt_aliases.items():
            prompt = prompt.replace(old, new)
        return super().create_video(prompt, image, output, duration, additional_images=additional_images, reference_audios=reference_audios)

    def _video_payload(self, *args, **kwargs):
        payload = super()._video_payload(*args, **kwargs)
        payload["ratio"] = self.frame["video_ratio"]
        # The tier's resolution (480p fast / 720p) unless the model only serves one.
        payload["resolution"] = VIDEO_MODEL_LIMITS.get(self.settings.video_model, {}).get("resolution", self.resolution)
        return payload

    def create_image(self, prompt, output, reference=None, additional_references=()):
        # Character cards stay portrait (identity references); scene cards
        # take the frame's aspect so a landscape episode gets a landscape set.
        self._tls.ratio = self.frame["image_ratio"] if Path(output).name.startswith("establishing") else "9:16"
        try:
            if additional_references:
                return super().create_image(prompt, output, reference=reference, additional_references=additional_references)
            return super().create_image(prompt, output, reference=reference)
        finally:
            self._tls.ratio = None

class FramedLocalH3(FramedPhanRouter):
    """Cards through PhanRouter as before; video from the local H3 service.

    Composition rather than a second base class: the picture path and the video path share
    nothing, and one line naming the object that answers create_video reads better than a
    method resolution order that has to be worked out.
    """

    def __init__(self, settings, frame: dict, base_url: str, resolution: str = "720p"):
        super().__init__(settings, frame, resolution)
        self.local = LocalH3MediaProvider(settings, base_url, ratio=frame["video_ratio"])

    def create_video(self, prompt, image, output, duration, additional_images=(), reference_audios=(), seed_variant=0):
        for old, new in self.prompt_aliases.items():
            prompt = prompt.replace(old, new)
        return self.local.create_video(prompt, image, output, duration,
                                       additional_images=additional_images, reference_audios=reference_audios, seed_variant=seed_variant)
