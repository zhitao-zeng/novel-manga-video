"""Cards from the local Qwen-Image-2.1 service (qwen-image-21/serve.py).

Only create_image.  The service is picked up wherever a card is drawn, and video
keeps going wherever it already went - the two paths share nothing.

Why a resident service rather than the existing command provider: a card costs
about a minute of drawing and the pipeline is another minute of loading, so a
book of eighty characters would spend more time loading the model than using it.
"""
from __future__ import annotations

import base64
import io
import os
from pathlib import Path

import httpx
from PIL import Image

from ..config import Settings
from .base import ImageResult, image_dimensions

# Recorded in each card's request.json so switching backends invalidates the cache.
# The name, not the URL: the same service on another port draws the same picture.
LOCAL_IMAGE_MODEL = "qwen-image-2.1"
MAX_REFERENCES = 10          # the pipeline's own limit
MAX_BYTES = 64 * 1024 * 1024


class LocalQwenImageProvider:
    def __init__(self, settings: Settings, base_url: str) -> None:
        self.settings = settings
        self.base_url = base_url.strip().rstrip("/")
        # Direct, like the other providers' clients: the session proxy has no business
        # tunnelling a call to a service on this machine, and .env lists the host in the
        # uppercase NO_PROXY, which httpx does not read.
        self.client = httpx.Client(timeout=settings.request_timeout, trust_env=False)

    def create_image(
        self,
        prompt: str,
        output: Path,
        reference: Path | None = None,
        additional_references: tuple[Path, ...] = (),
        *, aspect_ratio: str | None = None,
    ) -> ImageResult:
        width, height = image_dimensions(aspect_ratio or "9:16")
        references = [
            Path(path) for path in (reference, *additional_references)
            if path and Path(path).is_file()
        ]
        if len(references) > MAX_REFERENCES:
            raise RuntimeError(
                f"local Qwen image service accepts at most {MAX_REFERENCES} references, "
                f"got {len(references)}"
            )
        payload = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "references": [
                base64.b64encode(path.read_bytes()).decode("ascii") for path in references
            ],
        }
        try:
            response = self.client.post(f"{self.base_url}/generate", json=payload)
        except httpx.HTTPError as error:
            raise RuntimeError(f"local Qwen image service unreachable at {self.base_url}: {error}") from error
        if response.status_code != 200:
            raise RuntimeError(
                f"local Qwen image service returned {response.status_code}: {response.text[:400]}"
            )
        if len(response.content) > MAX_BYTES:
            raise RuntimeError(f"local Qwen image service returned more than {MAX_BYTES} bytes")
        self._write(response.content, output, (width, height))
        return ImageResult(path=output)

    @staticmethod
    def _write(png: bytes, output: Path, size: tuple[int, int]) -> None:
        """The service answers in PNG; cards on disk are .jpeg.  Writing PNG bytes under a
        .jpeg name would leave a file whose contents and extension disagree, which the card
        upload and anything reading by extension would then have to guess about."""
        suffix = output.suffix.casefold()
        if suffix not in (".png", ".jpg", ".jpeg"):
            raise RuntimeError(f"local Qwen image service cannot write {suffix or 'a suffixless file'}: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        partial = output.with_suffix(output.suffix + ".partial")
        with Image.open(io.BytesIO(png)) as image:
            # The service draws at a size the pipeline accepts (a multiple of 32) and scales
            # back itself, so this is normally a no-op - but the card's shape is what every
            # later shot is framed against, and the caller was promised this one.
            if image.size != size:
                image = image.resize(size, Image.LANCZOS)
            if suffix == ".png":
                image.save(partial, format="PNG")
            else:
                # Flat colour with hard edges is exactly what JPEG rings around, and the cards
                # are the reference every later shot is locked to; keep the quality high and
                # the chroma unsubsampled.
                image.convert("RGB").save(partial, format="JPEG", quality=95, subsampling=0)
        os.replace(partial, output)
