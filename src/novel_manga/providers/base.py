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
    def enter_stage(self, stage: str) -> None:
        """Prepare one model family for a batch of related requests.

        Hosted and mock providers intentionally use this no-op. A local
        single-GPU adapter may override it to unload the previous checkpoint
        and load the model family needed by ``stage``.
        """

    @abstractmethod
    def create_image(self, prompt: str, output: Path, reference: Path | None = None) -> ImageResult: ...

    @abstractmethod
    def create_video(
        self,
        prompt: str,
        image: ImageResult,
        output: Path,
        duration: float,
        reference_audio: Path | None = None,
        additional_images: tuple[Path, ...] = (),
    ) -> Path: ...

    @abstractmethod
    def synthesize(
        self,
        text: str,
        output: Path,
        *,
        voice: str | None = None,
        instructions: str | None = None,
        speed: float | None = None,
    ) -> Path: ...


@dataclass
class ProviderBundle:
    planner: Planner
    media: MediaProvider
