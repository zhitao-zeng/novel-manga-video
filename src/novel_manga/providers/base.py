from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path



@dataclass(frozen=True)
class ImageResult:
    path: Path
    public_url: str | None = None


def image_dimensions(aspect_ratio: str) -> tuple[int, int]:
    """The two frame sizes supported by current production profiles."""
    return {"9:16": (1080, 1920), "16:9": (1920, 1080)}[aspect_ratio]


class MediaProvider(ABC):
    @abstractmethod
    def create_image(
        self,
        prompt: str,
        output: Path,
        reference: Path | None = None,
        additional_references: tuple[Path, ...] = (),
        *, aspect_ratio: str | None = None,
    ) -> ImageResult: ...

    @abstractmethod
    def create_video(
        self,
        prompt: str,
        image: ImageResult,
        output: Path,
        duration: float,
        additional_images: tuple[Path, ...] = (),
    ) -> Path: ...
