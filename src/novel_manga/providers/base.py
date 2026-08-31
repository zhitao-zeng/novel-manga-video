from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..models import EpisodePlan, NovelDocument, StoryBible
from ..planner import Planner


@dataclass(frozen=True)
class ImageResult:
    path: Path
    public_url: str | None = None


class MediaProvider(ABC):
    @abstractmethod
    def create_image(
        self,
        prompt: str,
        output: Path,
        reference: Path | None = None,
        additional_references: tuple[Path, ...] = (),
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

@dataclass
class ProviderBundle:
    planner: Planner
    media: MediaProvider
