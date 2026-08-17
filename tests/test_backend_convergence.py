from __future__ import annotations

import json
import sys
from pathlib import Path

from novel_manga.api import ApiConfig, JobManager
from novel_manga.config import Settings
from novel_manga.models import Character, StoryBible
from novel_manga.production import SeriesAssetFactory
from novel_manga.providers.base import ImageResult
from novel_manga.providers.command import CommandMediaProvider


def test_series_assets_batch_base_before_edit(tmp_path: Path) -> None:
    class Provider:
        events: list[str] = []

        def enter_stage(self, stage: str) -> None:
            self.events.append(f"stage:{stage}")

        def create_image(self, prompt, output, reference=None):
            self.events.append("image:edit" if reference else "image:base")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"jpeg")
            return ImageResult(path=output)

    provider = Provider()
    factory = SeriesAssetFactory(Settings(), provider)  # type: ignore[arg-type]
    bible = StoryBible(
        novel_title="统一链路",
        genre="剧情",
        visual_style="二维国漫",
        palette="青蓝",
        characters=[
            Character(name="甲", appearance="黑发青年", wardrobe="黑衣"),
            Character(name="乙", appearance="长发女子", wardrobe="蓝衣"),
        ],
        locations=["庭院", "书房"],
        style_fingerprint="shared-core",
    )

    factory.build(tmp_path / "series_assets", bible)

    assert provider.events == [
        "stage:image-base",
        "image:base",
        "image:base",
        "image:base",
        "image:base",
        "stage:image-edit",
        "image:edit",
        "image:edit",
    ]


def test_command_provider_forwards_lifecycle_and_speech_speed(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--stage')
parser.add_argument('--text')
parser.add_argument('--voice')
parser.add_argument('--instructions')
parser.add_argument('--speed')
parser.add_argument('--output', type=Path)
args = parser.parse_args()
if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(vars(args), default=str), encoding='utf-8')
""".strip(),
        encoding="utf-8",
    )
    command = f"{sys.executable} {adapter}"
    provider = CommandMediaProvider(
        Settings(
            provider="command",
            image_command=command,
            video_command=command,
            tts_command=command,
            model_lifecycle_command=command,
        )
    )
    output = tmp_path / "voice.json"

    provider.enter_stage("audio")
    provider.synthesize("一句台词", output, voice="deep_male", speed=1.15)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["text"] == "一句台词"
    assert payload["voice"] == "deep_male"
    assert payload["speed"] == "1.150"


def test_command_provider_forwards_h3_reference_audio(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--prompt')
parser.add_argument('--image')
parser.add_argument('--additional-image', action='append')
parser.add_argument('--reference-audio')
parser.add_argument('--duration')
parser.add_argument('--fps')
parser.add_argument('--width')
parser.add_argument('--height')
parser.add_argument('--output', type=Path)
args = parser.parse_args()
args.output.write_text(json.dumps(vars(args), default=str), encoding='utf-8')
""".strip(),
        encoding="utf-8",
    )
    command = f"{sys.executable} {adapter}"
    provider = CommandMediaProvider(
        Settings(
            provider="command",
            image_command=command,
            video_command=command,
            tts_command=command,
        )
    )
    image = tmp_path / "shot.jpeg"
    audio = tmp_path / "driver.wav"
    output = tmp_path / "clip.json"
    image.write_bytes(b"jpeg")
    audio.write_bytes(b"wav")

    provider.create_video(
        "动作链",
        ImageResult(path=image),
        output,
        duration=8.0,
        reference_audio=audio,
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["reference_audio"] == str(audio)


def test_command_provider_forwards_separate_h3_assets(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--prompt')
parser.add_argument('--image')
parser.add_argument('--additional-image', action='append')
parser.add_argument('--reference-audio')
parser.add_argument('--duration')
parser.add_argument('--fps')
parser.add_argument('--width')
parser.add_argument('--height')
parser.add_argument('--output', type=Path)
args = parser.parse_args()
args.output.write_text(json.dumps(vars(args), default=str), encoding='utf-8')
""".strip(),
        encoding="utf-8",
    )
    command = f"{sys.executable} {adapter}"
    provider = CommandMediaProvider(
        Settings(
            provider="command",
            image_command=command,
            video_command=command,
            tts_command=command,
        )
    )
    character = tmp_path / "character.jpeg"
    scene = tmp_path / "scene.jpeg"
    audio = tmp_path / "driver.wav"
    output = tmp_path / "clip.json"
    for path in (character, scene, audio):
        path.write_bytes(b"asset")

    provider.create_video(
        "单人对白动作链",
        ImageResult(path=character),
        output,
        duration=4.0,
        reference_audio=audio,
        additional_images=(scene,),
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["image"] == str(character)
    assert payload["additional_image"] == [str(scene)]


def test_local_model_readiness_fails_closed(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / "output"
    manager = JobManager(
        ApiConfig(
            output_root=output,
            upload_root=output / ".uploads",
            state_root=output / ".jobs",
            require_local_models=True,
            model_supervisor_url="http://127.0.0.1:9",
        )
    )
    manager.start()
    try:
        status = manager.runtime_status()
        assert status["required"] is True
        assert status["ready"] is False
        assert manager.ready is False
    finally:
        manager.shutdown()
