from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from novel_manga.api import ApiConfig, create_app
from novel_manga.ingest import read_novel
from novel_manga.models import EpisodeStatus, SubmissionManifest, VideoRecord
from novel_manga.util import atomic_write_json


REQUIRED_WIDTH = 1080
REQUIRED_HEIGHT = 1920
REQUIRED_FPS = (25, 30)


def _starting_kit_check_video_validity(video_path: Path) -> tuple[bool, str]:
    """Mirror feat/0.0.1 run_for_darvin.py admission semantics exactly."""
    if not video_path.exists():
        return False, "文件不存在"
    if video_path.stat().st_size == 0:
        return False, "视频文件为空"
    if video_path.suffix.lower() != ".mp4":
        return False, f"格式错误: {video_path.suffix.lower()}（应为 .mp4）"

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-of",
            "json",
            str(video_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        # The official judge accepts a non-empty MP4 when ffprobe is unavailable.
        return True, "通过（ffprobe 不可用，仅校验格式和大小）"

    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        return True, "通过（ffprobe 不可用，仅校验格式和大小）"
    stream = streams[0]
    width = stream.get("width", 0)
    height = stream.get("height", 0)
    rate = stream.get("r_frame_rate", "0/1")
    if "/" in rate:
        numerator, denominator = rate.split("/")
        fps = float(numerator) / float(denominator) if float(denominator) else 0
    else:
        fps = float(rate)

    if width != REQUIRED_WIDTH or height != REQUIRED_HEIGHT:
        return False, f"分辨率错误: {width}x{height}"
    if int(fps) not in REQUIRED_FPS:
        return False, f"帧率错误: {fps}fps"
    if width * 16 != height * 9:
        return False, f"比例错误: {width}:{height}"
    return True, "通过"


def _make_video(path: Path, *, fps: int) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c=navy:s={REQUIRED_WIDTH}x{REQUIRED_HEIGHT}:r={fps}:d=0.24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=0.24",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _runner(output_root: Path):
    def run(source: Path, novel_id: str, title: str) -> SubmissionManifest:
        novel = read_novel(source, novel_id=novel_id, title=title)
        episode = novel.episodes[0]
        video_id = f"{novel_id}_1"
        episode_dir = output_root / novel_id / video_id
        episode_dir.mkdir(parents=True, exist_ok=True)
        video = episode_dir / f"{video_id}.mp4"
        cover = episode_dir / f"{video_id}_cover.jpeg"
        ending = episode_dir / f"{video_id}_ending.jpeg"
        _make_video(video, fps=25)
        Image.new("RGB", (REQUIRED_WIDTH, REQUIRED_HEIGHT), "navy").save(
            cover, format="JPEG"
        )
        Image.new("RGB", (REQUIRED_WIDTH, REQUIRED_HEIGHT), "black").save(
            ending, format="JPEG"
        )
        manifest = SubmissionManifest(
            novel_id=novel_id,
            novel_title=novel.title,
            video_count=1,
            videos=[
                VideoRecord(
                    video_id=video_id,
                    video_title=episode.source_title,
                    video_cover=f"{video_id}/{cover.name}",
                    ending_screen=f"{video_id}/{ending.name}",
                    video_file=f"{video_id}/{video.name}",
                    text_count=episode.text_count,
                    status=EpisodeStatus.SUCCEEDED,
                )
            ],
        )
        atomic_write_json(
            output_root / novel_id / "manifest.json",
            manifest.model_dump(mode="json"),
        )
        return manifest

    return run


def _wait(client: TestClient, novel_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        payload = client.get(
            "/generate_progress", params={"novel_id": novel_id}
        ).json()
        if payload.get("finished"):
            return payload
        time.sleep(0.02)
    raise AssertionError("generation did not finish")


def test_starting_kit_download_ids_and_video_admission(tmp_path: Path):
    output = tmp_path / "output"
    config = ApiConfig(
        output_root=output,
        upload_root=output / ".uploads",
        state_root=output / ".jobs",
        provider="mock",
    )
    app = create_app(config, runner=_runner(output))

    with TestClient(app) as client:
        assert client.get("/ready").status_code == 200
        response = client.post(
            "/upload_novel",
            data={"novel_id": "19"},
            files={"file": ("雨后小故事.txt", "雨停以后，她推开了门。".encode())},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

        progress = _wait(client, "19")
        assert progress["status"] == "completed"
        assert progress["completed_count"] == progress["total_expected"] == 1
        video_info = progress["video_list"][0]
        video_id = video_info["video_id"]

        # run_for_darvin ignores the advertised image names and derives these IDs.
        downloads = {
            "video": client.get(f"/download/video/{video_id}"),
            "cover": client.get(f"/download/image/{video_id}_cover"),
            "ending": client.get(f"/download/image/{video_id}_ending"),
        }
        assert downloads["video"].headers["content-type"] == "video/mp4"
        assert downloads["cover"].headers["content-type"] == "image/jpeg"
        assert downloads["ending"].headers["content-type"] == "image/jpeg"

        downloaded_video = tmp_path / f"{video_id}.mp4"
        downloaded_video.write_bytes(downloads["video"].content)
        assert _starting_kit_check_video_validity(downloaded_video) == (True, "通过")

        for name in ("cover", "ending"):
            downloaded_image = tmp_path / f"{video_id}_{name}.jpg"
            downloaded_image.write_bytes(downloads[name].content)
            with Image.open(downloaded_image) as image:
                assert image.format == "JPEG"
                assert image.size == (REQUIRED_WIDTH, REQUIRED_HEIGHT)


def test_starting_kit_rejects_unsupported_frame_rate(tmp_path: Path):
    video = tmp_path / "24fps.mp4"
    _make_video(video, fps=24)
    valid, message = _starting_kit_check_video_validity(video)
    assert valid is False
    assert message.startswith("帧率错误: 24.0fps")
