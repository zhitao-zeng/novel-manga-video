import json
import time
from types import SimpleNamespace

import httpx

from experiments import benchmark_seedance_speed as probe


def _setup(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(probe.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("PHANROUTER_API_KEY", "test-only-key")
    monkeypatch.setenv("PHANROUTER_BASE_URL", "https://model.invalid")
    return ({"parameters": {"duration": 15}, "task_timeout_seconds": 60, "poll_interval_seconds": 0},
            {"id": "case", "prompt": "test", "references": [], "spoken_text": "test"})


def test_ambiguous_submission_is_never_repeated(monkeypatch, tmp_path):
    calls = []

    def model(request):
        calls.append(request.method)
        raise httpx.ReadTimeout("timed out after sending request")

    config, case = _setup(monkeypatch, model)
    first = probe.run_one(tmp_path, config, case, "sd2.0-fast", time.time() + 60)
    second = probe.run_one(tmp_path, config, case, "sd2.0-fast", time.time() + 60)
    assert first["status"] == second["status"] == "submission_unknown"
    assert calls == ["POST"]


def test_rejected_model_is_not_retried(monkeypatch, tmp_path):
    calls = []

    def model(request):
        calls.append(request.method)
        return httpx.Response(403, json={"error": "model not available"})

    config, case = _setup(monkeypatch, model)
    for _ in range(2):
        result = probe.run_one(tmp_path, config, case, "sd2.0-fast", time.time() + 60)
        assert result["status"] == "submission_rejected"
    assert calls == ["POST"]


def test_resume_polls_existing_task_without_new_generation(monkeypatch, tmp_path):
    calls = []

    def model(request):
        calls.append(request.method)
        return httpx.Response(200, json={"status": "succeeded", "url": "https://media.invalid/video.mp4", "usage": {"total_tokens": 123}})

    config, case = _setup(monkeypatch, model)
    directory = tmp_path / "runs/case/sd2.5"
    directory.mkdir(parents=True)
    (directory / "result.json").write_text(json.dumps({"case": "case", "model": "sd2.5", "status": "interrupted", "task_id": "existing", "submitted_epoch": time.time()}))
    monkeypatch.setattr(probe, "download", lambda url, path: path.write_bytes(b"video fixture"))
    monkeypatch.setattr(probe, "evaluate", lambda *args: {"usable": True})
    result = probe.run_one(tmp_path, config, case, "sd2.5", time.time() + 60)
    assert result["status"] == "completed"
    assert result["usage"]["total_tokens"] == 123
    assert calls == ["GET"]


def test_native_480p_padding_is_reported_without_failing_valid_media(monkeypatch, tmp_path):
    media = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 864, "height": 496, "r_frame_rate": "24/1"},
                         {"codec_type": "audio", "codec_name": "aac"}], "format": {"duration": "15.104"}}
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=json.dumps(media)))
    monkeypatch.setattr(probe, "audio_levels", lambda _: (-28, -5))
    monkeypatch.setenv("NOVEL_ASR_COMMAND", "unused-asr-command")
    (tmp_path / "asr.json").write_text(json.dumps({"hypothesis": "你好", "backend": "test"}))
    result = probe.evaluate(tmp_path, {"parameters": {"duration": 15}}, {"spoken_text": "你好"}, tmp_path)
    assert result["requested_resolution_exact"] is False
    assert result["media_passed"] and result["speech_passed"] and result["usable"]
