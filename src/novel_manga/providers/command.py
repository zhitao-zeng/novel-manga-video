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

    def create_image(self, prompt: str, output: Path, reference: Path | None = None) -> ImageResult:
        arguments = ["--prompt", prompt, "--width", str(self.settings.width), "--height", str(self.settings.height)]
        if reference:
            arguments.extend(["--reference", str(reference)])
        self._run(self.settings.image_command, arguments, output)
        return ImageResult(path=output)

    def create_video(
        self,
        prompt: str,
        image: ImageResult,
        output: Path,
        duration: float,
        reference_audio: Path | None = None,
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
        self._run(self.settings.video_command, arguments, output)
        return output

    def synthesize(
        self,
        text: str,
        output: Path,
        *,
        voice: str | None = None,
        instructions: str | None = None,
    ) -> Path:
        arguments = ["--text", text, "--voice", voice or self.settings.tts_voice]
        if instructions:
            arguments.extend(["--instructions", instructions])
        self._run(self.settings.tts_command, arguments, output)
        return output
