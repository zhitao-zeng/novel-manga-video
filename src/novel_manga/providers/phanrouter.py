"""PhanRouter provider entry: compose reference, image and video services."""
from __future__ import annotations

from pathlib import Path
import httpx
from ..config import Settings
from .base import MediaProvider, ImageResult
from . import phanrouter_images as images
from . import phanrouter_video as video
from .phanrouter_references import ReferenceMaterials
from .downloads import download_file

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


    def _download(self, url, output, max_bytes=512 * 1024 * 1024, *, client=None):
        return download_file(client or self.client, url, output, max_bytes=max_bytes)

    def _references(self):
        return ReferenceMaterials(self.settings, self.client, self.video_headers, self.image_headers)

    def _restore_image_url(self, image):
        return self._references()._restore_image_url(image)

    def public_card_url(self, path, digest):
        return self._references().public_card_url(path, digest)

    def _video_payload(self, prompt, image_url, duration, additional_image_urls=(), reference_audio_urls=()):
        return video.video_payload(self.settings, prompt, image_url, duration, additional_image_urls, reference_audio_urls)

    def create_image(self, prompt, output, reference=None, additional_references=()):
        return images.create_image(self.settings, self.client, self.image_headers, prompt, output, reference, additional_references)

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
            tuple(video.audio_data_url(path) for path in reference_audios),
        )
        return video.generate_video(self.settings, self.client, self.video_headers, payload, output, additional_images, reference_audios)
