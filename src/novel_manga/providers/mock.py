from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..config import Settings
from ..util import run
from .base import ImageResult, MediaProvider


class MockMediaProvider(MediaProvider):
    """Deterministic local provider for tests; outputs are explicitly non-submission previews."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _font(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self.settings.font_path), size)

    def create_image(
        self,
        prompt: str,
        output: Path,
        reference: Path | None = None,
        additional_references: tuple[Path, ...] = (),
    ) -> ImageResult:
        output.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(prompt.encode("utf-8")).digest()
        top = tuple(35 + byte // 3 for byte in digest[:3])
        bottom = tuple(20 + byte // 4 for byte in digest[3:6])
        image = Image.new("RGB", (self.settings.width, self.settings.height), top)
        pixels = image.load()
        for y in range(self.settings.height):
            ratio = y / max(1, self.settings.height - 1)
            color = tuple(round(top[c] * (1 - ratio) + bottom[c] * ratio) for c in range(3))
            for x in range(self.settings.width):
                pixels[x, y] = color
        draw = ImageDraw.Draw(image, "RGBA")
        draw.ellipse((170, 250, 910, 990), fill=(245, 221, 198, 255), outline=(25, 28, 40, 255), width=14)
        draw.polygon([(270, 420), (540, 170), (810, 420)], fill=(35, 32, 48, 255))
        draw.rounded_rectangle((210, 960, 870, 1690), radius=100, fill=(35, 70, 110, 255), outline=(230, 210, 150, 255), width=12)
        draw.text(
            (34, 36), "MOCK PREVIEW", font=self._font(30), fill=(255, 255, 255, 180),
            stroke_width=2, stroke_fill=(0, 0, 0, 170),
        )
        image.save(output, "JPEG", quality=95, subsampling=0)
        return ImageResult(path=output)

    def create_video(
        self,
        prompt: str,
        image: ImageResult,
        output: Path,
        duration: float,
        additional_images: tuple[Path, ...] = (),
    ) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        frames = max(1, round(duration * self.settings.fps))
        run([
            "ffmpeg", "-y", "-loop", "1", "-i", str(image.path),
            "-vf", (
                f"scale={self.settings.width}:{self.settings.height}:force_original_aspect_ratio=increase,"
                f"crop={self.settings.width}:{self.settings.height},"
                f"zoompan=z='min(zoom+0.00035,1.06)':d={frames}:"
                f"s={self.settings.width}x{self.settings.height}:fps={self.settings.fps},format=yuv420p"
            ),
            "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "20", "-movflags", "+faststart", str(output),
        ])
        return output
