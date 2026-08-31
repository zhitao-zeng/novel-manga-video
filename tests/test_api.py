from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from novel_manga.api import ApiConfig, JobManager, JobState, create_app
from novel_manga.ingest import read_novel
from novel_manga.models import EpisodeStatus, SubmissionManifest, VideoRecord
from novel_manga.util import atomic_write_json


def _config(tmp_path: Path) -> ApiConfig:
    output = tmp_path / "output"
    return ApiConfig(
        output_root=output,
        upload_root=output / ".uploads",
        state_root=output / ".jobs",
        provider="mock",
        job_workers=1,
        max_upload_bytes=1024 * 1024,
    )


def _fake_runner(output_root: Path):
    def run(source: Path, novel_id: str, title: str) -> SubmissionManifest:
        novel = read_novel(source, novel_id=novel_id, title=title)
        records: list[VideoRecord] = []
        for episode in novel.episodes:
            video_id = f"{novel_id}_{episode.index}"
            episode_dir = output_root / novel_id / video_id
            episode_dir.mkdir(parents=True, exist_ok=True)
            video = episode_dir / f"{video_id}.mp4"
            cover = episode_dir / f"{video_id}_cover.jpeg"
            ending = episode_dir / f"{video_id}_ending.jpeg"
            video.write_bytes(b"\x00\x00\x00\x18ftypisomfake-video")
            Image.new("RGB", (16, 24), "navy").save(cover, format="JPEG")
            Image.new("RGB", (16, 24), "black").save(ending, format="JPEG")
            records.append(
                VideoRecord(
                    video_id=video_id,
                    video_title=episode.source_title,
                    video_cover=f"{video_id}/{video_id}_cover.jpeg",
                    ending_screen=f"{video_id}/{video_id}_ending.jpeg",
                    video_file=f"{video_id}/{video_id}.mp4",
                    text_count=episode.text_count,
                    status=EpisodeStatus.SUCCEEDED,
                )
            )
        manifest = SubmissionManifest(
            novel_id=novel_id,
            novel_title=novel.title,
            video_count=len(records),
            videos=records,
        )
        atomic_write_json(
            output_root / novel_id / "manifest.json",
            manifest.model_dump(mode="json"),
        )
        return manifest

    return run


def _wait_for_terminal(client: TestClient, novel_id: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get("/generate_progress", params={"novel_id": novel_id})
        assert response.status_code == 200
        payload = response.json()
        if payload["finished"]:
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_health_upload_progress_and_download_contract(tmp_path: Path):
    config = _config(tmp_path)
    app = create_app(config, runner=_fake_runner(config.output_root))
    source = "书名\n\n第一章 初入江湖\n少年推门而入。\n\n第二章 风云变幻\n众人终于知道真相。"

    with TestClient(app) as client:
        ready = client.get("/ready")
        assert ready.status_code == 200
        assert ready.json() == {"success": True, "status": "ready"}

        uploaded = client.post(
            "/upload_novel",
            data={"novel_id": "小说_1"},
            files={"file": ("示例.md", source.encode(), "text/markdown")},
        )
        assert uploaded.status_code == 200
        assert uploaded.json() == {
            "success": True,
            "novel_id": "小说_1",
            "message": "视频生成任务已启动，novel_id=小说_1",
        }

        progress = _wait_for_terminal(client, "小说_1")
        assert set(progress) == {
            "success",
            "novel_id",
            "status",
            "finished",
            "completed_count",
            "total_expected",
            "video_list",
        }
        assert progress["status"] == "completed"
        assert progress["finished"] is True
        assert progress["completed_count"] == 2
        assert progress["total_expected"] == 2
        assert [video["video_id"] for video in progress["video_list"]] == [
            "小说_1_1",
            "小说_1_2",
        ]
        assert progress["video_list"][0] == {
            "video_id": "小说_1_1",
            "video_title": "第一章 初入江湖",
            "video_cover": "小说_1_1_cover.jpg",
            "ending_screen": "小说_1_1_ending.jpg",
            "text": "书名\n\n第一章 初入江湖\n少年推门而入。",
        }

        video = client.get("/download/video/小说_1_1")
        assert video.status_code == 200
        assert video.headers["content-type"] == "video/mp4"
        assert video.content.startswith(b"\x00\x00\x00\x18ftypisom")

        cover = client.get("/download/image/小说_1_1_cover")
        assert cover.status_code == 200
        assert cover.headers["content-type"] == "image/jpeg"
        assert cover.content.startswith(b"\xff\xd8")

        ending = client.get("/download/image/小说_1_1_ending")
        assert ending.status_code == 200
        assert ending.headers["content-type"] == "image/jpeg"
        assert ending.content.startswith(b"\xff\xd8")

        rerun = client.post(
            "/upload_novel",
            data={"novel_id": "小说_1"},
            files={"file": ("新版.txt", "新的一集正文。".encode(), "text/plain")},
        )
        assert rerun.status_code == 200
        rerun_progress = _wait_for_terminal(client, "小说_1")
        assert rerun_progress["completed_count"] == 1
        assert rerun_progress["total_expected"] == 1
        assert rerun_progress["video_list"][0]["text"] == "新的一集正文。"
        assert list((config.output_root / "小说_1").glob("manifest.previous-*.json"))


def test_upload_validation_and_not_found_responses(tmp_path: Path):
    config = _config(tmp_path)
    app = create_app(config, runner=_fake_runner(config.output_root))

    with TestClient(app) as client:
        missing_file = client.post("/upload_novel", data={"novel_id": "1"})
        assert missing_file.status_code == 400
        assert missing_file.json() == {"success": False, "message": "缺少文件参数 file"}

        missing_id = client.post(
            "/upload_novel",
            files={"file": ("novel.txt", b"content", "text/plain")},
        )
        assert missing_id.status_code == 400
        assert missing_id.json()["success"] is False

        unsafe_id = client.post(
            "/upload_novel",
            data={"novel_id": "../escape"},
            files={"file": ("novel.txt", b"content", "text/plain")},
        )
        assert unsafe_id.status_code == 400
        assert unsafe_id.json()["success"] is False

        bad_suffix = client.post(
            "/upload_novel",
            data={"novel_id": "1"},
            files={"file": ("novel.exe", b"content", "application/octet-stream")},
        )
        assert bad_suffix.status_code == 400
        assert bad_suffix.json()["success"] is False

        unknown_progress = client.get("/generate_progress", params={"novel_id": "missing"})
        assert unknown_progress.status_code == 404
        assert unknown_progress.json() == {
            "success": False,
            "message": "生成任务不存在: missing",
        }

        unknown_video = client.get("/download/video/1_99")
        assert unknown_video.status_code == 404
        assert unknown_video.json() == {
            "success": False,
            "message": "视频文件不存在: 1_99",
        }

        unknown_type = client.get("/download/document/1_1")
        assert unknown_type.status_code == 400
        assert unknown_type.json()["success"] is False


def test_failed_pipeline_is_reported_as_finished_failed(tmp_path: Path):
    config = _config(tmp_path)

    def fail(_: Path, __: str, ___: str) -> SubmissionManifest:
        raise RuntimeError("backend unavailable")

    app = create_app(config, runner=fail)
    with TestClient(app) as client:
        uploaded = client.post(
            "/upload_novel",
            data={"novel_id": "failure"},
            files={"file": ("novel.txt", "一句正文。".encode(), "text/plain")},
        )
        assert uploaded.status_code == 200
        progress = _wait_for_terminal(client, "failure")
        assert progress == {
            "success": True,
            "novel_id": "failure",
            "status": "failed",
            "finished": True,
            "completed_count": 0,
            "total_expected": 1,
            "video_list": [],
        }


def test_processing_job_is_recovered_from_durable_state(tmp_path: Path):
    config = _config(tmp_path)
    config.output_root.mkdir(parents=True)
    config.upload_root.mkdir(parents=True)
    config.state_root.mkdir(parents=True)
    source = config.upload_root / "recover.txt"
    source.write_text("恢复后的正文。", encoding="utf-8")
    manager = JobManager(config, runner=_fake_runner(config.output_root))
    state = JobState(
        run_id="recover",
        novel_id="recover",
        original_filename="recover.txt",
        novel_title="恢复任务",
        upload_path=str(source),
        total_expected=1,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    manager._write_state_unlocked(state)
    app = create_app(config, manager=manager)

    with TestClient(app) as client:
        progress = _wait_for_terminal(client, "recover")
        assert progress["status"] == "completed"
        assert progress["video_list"][0]["text"] == "恢复后的正文。"
