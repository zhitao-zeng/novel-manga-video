from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from ..config import Settings
from .base import ImageResult, MediaProvider


class CommandMediaProvider(MediaProvider):
    """Local command adapter for arbitrary image, video, and TTS backends."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.remote_image_provider = None
        normalized_image_model = "".join(
            character
            for character in settings.image_model.casefold()
            if character.isalnum()
        )
        if normalized_image_model == "gptimage2" and (
            settings.phanrouter_image_api_key or settings.phanrouter_api_key
        ):
            from .phanrouter import PhanRouterMediaProvider

            self.remote_image_provider = PhanRouterMediaProvider(settings)

    def enter_stage(self, stage: str) -> None:
        if self.remote_image_provider is not None and stage in {
            "image-base",
            "image-edit",
        }:
            return
        if not self.settings.model_lifecycle_command:
            return
        subprocess.run(
            shlex.split(self.settings.model_lifecycle_command) + ["--stage", stage],
            check=True,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def _run(command: str | None, arguments: list[str], output: Path) -> None:
        if not command:
            raise RuntimeError("provider command is missing")
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            shlex.split(command) + arguments + ["--output", str(output)],
            check=True,
            capture_output=True,
            text=True,
        )
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(f"provider command did not create a non-empty output: {output}")

    def create_image(
        self,
        prompt: str,
        output: Path,
        reference: Path | None = None,
        additional_references: tuple[Path, ...] = (),
    ) -> ImageResult:
        if self.remote_image_provider is not None:
            return self.remote_image_provider.create_image(
                prompt,
                output,
                reference=reference,
                additional_references=additional_references,
            )
        arguments = ["--prompt", prompt, "--width", str(self.settings.width), "--height", str(self.settings.height)]
        if self.settings.local_image_prompt_policy:
            arguments.extend(
                ["--prompt-policy", self.settings.local_image_prompt_policy]
            )
        if reference:
            arguments.extend(["--reference", str(reference)])
        for additional_reference in additional_references:
            arguments.extend(["--additional-reference", str(additional_reference)])
        self._run(self.settings.image_command, arguments, output)
        return ImageResult(path=output)

    def create_video(
        self,
        prompt: str,
        image: ImageResult,
        output: Path,
        duration: float,
        reference_audio: Path | None = None,
        additional_images: tuple[Path, ...] = (),
    ) -> Path:
        arguments = [
            "--prompt", prompt,
            "--image", str(image.path),
            "--duration", f"{duration:.6f}",
            "--fps", str(self.settings.fps),
            "--width", str(self.settings.width),
            "--height", str(self.settings.height),
        ]
        if reference_audio:
            arguments.extend(["--reference-audio", str(reference_audio)])
        for additional_image in additional_images:
            arguments.extend(["--additional-image", str(additional_image)])
        self._run(self.settings.video_command, arguments, output)
        return output

    def synthesize(
        self,
        text: str,
        output: Path,
        *,
        voice: str | None = None,
        instructions: str | None = None,
        speed: float | None = None,
    ) -> Path:
        arguments = ["--text", text, "--voice", voice or self.settings.tts_voice]
        if instructions:
            arguments.extend(["--instructions", instructions])
        if speed is not None:
            arguments.extend(["--speed", f"{speed:.3f}"])
        self._run(self.settings.tts_command, arguments, output)
        return output
