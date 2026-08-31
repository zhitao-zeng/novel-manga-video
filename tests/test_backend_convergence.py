from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from novel_manga.api import ApiConfig, JobManager
from novel_manga.config import Settings
from novel_manga.models import Character, StoryBible
from novel_manga.production import SeriesAssetFactory
from novel_manga.providers.base import ImageResult
from novel_manga.providers.command import CommandMediaProvider
from novel_manga.providers.phanrouter import PhanRouterMediaProvider


def test_series_assets_batch_base_before_edit(tmp_path: Path) -> None:
    class Provider:
        events: list[str] = []

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
        "image:base",
        "image:base",
        "image:base",
        "image:base",
        "image:edit",
        "image:edit",
    ]


def test_series_assets_inherit_optional_style_master_without_copying_identity(
    tmp_path: Path,
) -> None:
    class Provider:
        calls: list[tuple[str, Path | None]] = []

        def create_image(self, prompt, output, reference=None):
            self.calls.append((prompt, reference))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"jpeg")
            return ImageResult(path=output)

    master = tmp_path / "anime-master.jpeg"
    master.write_bytes(b"master")
    provider = Provider()
    factory = SeriesAssetFactory(
        Settings(style_master_path=master), provider  # type: ignore[arg-type]
    )
    bible = StoryBible(
        novel_title="统一动漫",
        genre="剧情",
        visual_style="二维国风赛璐璐动画",
        palette="高明度青蓝",
        characters=[Character(name="甲", appearance="黑发少年", wardrobe="黑衣")],
        locations=["庭院"],
        style_fingerprint="anime-v1",
    )

    factory.build(tmp_path / "series_assets", bible)

    assert provider.calls[0][1] == master
    assert provider.calls[1][1] == master
    assert "参考图只锁定线稿粗细" in provider.calls[0][0]
    assert "不得照抄参考图人物身份" in provider.calls[1][0]
    assert provider.calls[2][1].name == "turnaround.jpeg"


def test_command_provider_forwards_separate_image_edit_references(tmp_path: Path) -> None:
    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--prompt')
parser.add_argument('--prompt-policy')
parser.add_argument('--reference')
parser.add_argument('--additional-reference', action='append')
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
        )
    )
    character = tmp_path / "character.jpeg"
    location = tmp_path / "location.jpeg"
    output = tmp_path / "keyframe.json"

    provider.create_image(
        "剧情关键帧",
        output,
        reference=character,
        additional_references=(location,),
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["reference"] == str(character)
    assert payload["additional_reference"] == [str(location)]


def test_command_pipeline_routes_gpt_image_2_without_loading_local_image_stage(
    tmp_path: Path,
) -> None:
    class RemoteImage:
        calls = []

        def create_image(
            self, prompt, output, reference=None, additional_references=()
        ):
            self.calls.append((prompt, reference, additional_references))
            output.write_bytes(b"gpt-image-2")
            return ImageResult(path=output)

    provider = CommandMediaProvider(
        Settings(
            provider="command",
            image_model="gpt-image-2",
            phanrouter_image_api_key="runtime-only",
                image_command="/models/local-image",
                video_command="/models/video",
        )
    )
    assert provider.remote_image_provider is not None
    remote = RemoteImage()
    provider.remote_image_provider = remote  # type: ignore[assignment]
    character = tmp_path / "character.jpeg"
    location = tmp_path / "location.jpeg"
    output = tmp_path / "keyframe.jpeg"

    result = provider.create_image(
        "图1锁人物，图2锁空场",
        output,
        reference=character,
        additional_references=(location,),
    )

    assert result.path.read_bytes() == b"gpt-image-2"
    assert remote.calls == [
        ("图1锁人物，图2锁空场", character, (location,))
    ]


def test_phanrouter_forwards_gpt_image_references_as_two_urls(tmp_path: Path) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"task_id": "generated-task"}

    class Client:
        payload = None

        def post(self, url, headers=None, json=None):
            self.payload = json
            return Response()

    provider = object.__new__(PhanRouterMediaProvider)
    provider.settings = SimpleNamespace(
        image_model="gpt-image-2",
        phanrouter_base_url="https://example.invalid",
        inline_reference_images=False,
    )
    provider.client = Client()
    provider.image_headers = {}
    provider._poll_image_url = lambda task_id: f"https://images.invalid/{task_id}.jpeg"  # type: ignore[method-assign]
    provider._download = lambda url, output, max_bytes: output.write_bytes(b"image")  # type: ignore[method-assign]
    character = tmp_path / "character.jpeg"
    location = tmp_path / "location.jpeg"
    for path, task_id in ((character, "character-task"), (location, "location-task")):
        path.write_bytes(b"jpeg")
        path.with_suffix(path.suffix + ".task.json").write_text(
            json.dumps({"task_id": task_id}), encoding="utf-8"
        )

    result = provider.create_image(
        "图1锁身份，图2锁环境",
        tmp_path / "keyframe.jpeg",
        reference=character,
        additional_references=(location,),
    )

    assert result.path.read_bytes() == b"image"
    assert len(provider.client.payload["base64Files"]) == 2
    assert all(value == "anBlZw==" for value in provider.client.payload["base64Files"])
    assert "base64File" not in provider.client.payload
