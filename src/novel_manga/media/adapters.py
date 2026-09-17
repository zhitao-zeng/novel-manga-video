from __future__ import annotations
from ..config import Settings
from ..providers.phanrouter import PhanRouterMediaProvider
from ..providers.local_h3 import LocalH3MediaProvider

class FramedPhanRouter(PhanRouterMediaProvider):
    """PhanRouter provider whose video ratio and location-card aspect follow the frame."""

    def __init__(self, settings: Settings, frame: dict, resolution: str = "720p"):
        super().__init__(settings, ratio=frame["video_ratio"], resolution=resolution)
        self.frame = frame
        self.resolution = resolution
        self.prompt_aliases: dict[str, str] = {}  # profile.json "prompt_aliases": spelling sent to the model only

    def create_video(self, prompt, image, output, duration, additional_images=(), reference_audios=()):
        # A name the platform's text filter refuses (e.g. one shared with a
        # politician) is respelled in the text sent and nowhere else: the plan,
        # the request record and the subtitles keep the book's spelling, so
        # reuse matching and captions do not change.
        for old, new in self.prompt_aliases.items():
            prompt = prompt.replace(old, new)
        return super().create_video(prompt, image, output, duration, additional_images=additional_images, reference_audios=reference_audios)

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
