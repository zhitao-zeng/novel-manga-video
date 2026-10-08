from novel_manga.media.context import RenderContext, RenderOptions, RenderState
"""Episode doctor: liveness is measured with minimal real probes, never a listing.

Everything rides httpx.MockTransport; conftest fails any test that touches a
real transport, so a route left out of the table is a 404, not a phone call.
"""
import json
import os
from types import SimpleNamespace

import httpx
import pytest

from novel_manga.application.agents.episode import doctor
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.util import read_json

VIDEO_KEY, IMAGE_KEY, FLASH_KEY, DEEPSEEK_KEY = "video-secret", "image-secret", "flash-secret", "ds-secret"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, 'project_root', lambda: tmp_path)
    for name in list(os.environ):
        if name.startswith(("QWEN38_", "PHANROUTER_", "NOVEL_", "GPU81_", "GPU52_",
                            "SECOND_REVIEW", "H3_PROMPT")):
            monkeypatch.delenv(name, raising=False)


def make_model_dir(tmp_path):
    directory = tmp_path / "sensevoice"
    directory.mkdir()
    (directory / "model.int8.onnx").write_bytes(b"model")
    (directory / "tokens.txt").write_text("token", encoding="utf-8")
    return directory


def base_env(model_dir, **extra):
    return {
        "QWEN38_LOCAL_BASE_URL": "http://127.0.0.1:18120/v1,http://127.0.0.1:18121/v1",
        "GPU81_QWEN_API_KEY": FLASH_KEY,
        "GPU52_DEEPSEEK_API_KEY": DEEPSEEK_KEY,
        "PHANROUTER_API_KEY": VIDEO_KEY,
        "PHANROUTER_IMAGE_API_KEY": IMAGE_KEY,
        "NOVEL_SENSEVOICE_MODEL_DIR": str(model_dir),
        **extra,
    }


def client_for(routes):
    """routes: (method, host:port, path-prefix) -> status code, or 'down' to refuse."""
    def handle(request):
        for (method, host, prefix), answer in routes.items():
            if request.method == method and doctor._host(str(request.url)) == host \
                    and request.url.path.startswith(prefix):
                if answer == "down":
                    raise httpx.ConnectError("refused", request=request)
                return httpx.Response(answer, json={"data": []}, request=request)
        return httpx.Response(404, json={"data": []}, request=request)
    return httpx.Client(transport=httpx.MockTransport(handle))


def alive_routes():
    return {
        ("GET", "127.0.0.1:18120", "/v1/models"): 200,
        ("GET", "127.0.0.1:18121", "/v1/models"): 200,
        ("GET", "172.28.4.81:8038", "/v1/models"): 200,
        ("GET", "172.28.4.52:8831", "/v1/models"): 200,
        ("POST", "cloud.phanthy.com:443", "/phanrouter/api/v3/contents/generations/tasks"): 400,
        ("POST", "cloud.phanthy.com:443", "/phanrouter/v3/images/generations"): 400,
    }


def by_name(report):
    return {row["name"]: row for row in report["checks"]}


def test_check_endpoint_live_and_dead():
    live = doctor.check_endpoint("http://127.0.0.1:18120/v1", "text:qwen38_local[0]",
                                 client=client_for({("GET", "127.0.0.1:18120", "/v1/models"): 200}))
    assert live["ok"] is True and "HTTP 200" in live["detail"]
    dead = doctor.check_endpoint("http://127.0.0.1:18122/v1", "text:qwen38_local[2]",
                                 client=client_for({("GET", "127.0.0.1:18122", "/v1/models"): "down"}))
    assert dead["ok"] is False and "ConnectError" in dead["detail"]


def test_every_check_green_means_ok(tmp_path):
    report = doctor.run_doctor(tmp_path, client=client_for(alive_routes()),
                               environ=base_env(make_model_dir(tmp_path)))
    assert report["ok"] is True
    assert set(by_name(report)) == {
        "text:qwen38_local[0]", "text:qwen38_local[1]", "text:flashnext", "text:deepseek",
        "video:phanrouter_key", "image:phanrouter_key", "asr:model_dir", "env:manifest"}
    assert all(row["ok"] for row in report["checks"])


def test_details_carry_variables_and_hosts_but_never_keys(tmp_path):
    report = doctor.run_doctor(tmp_path, client=client_for(alive_routes()),
                               environ=base_env(make_model_dir(tmp_path)))
    blob = json.dumps(report)
    for secret in (VIDEO_KEY, IMAGE_KEY, FLASH_KEY, DEEPSEEK_KEY):
        assert secret not in blob
    rows = by_name(report)
    assert "QWEN38_LOCAL_BASE_URL[0] 127.0.0.1:18120" in rows["text:qwen38_local[0]"]["detail"]
    assert "flashnext 172.28.4.81:8038" in rows["text:flashnext"]["detail"]
    assert "deepseek 172.28.4.52:8831" in rows["text:deepseek"]["detail"]
    assert rows["video:phanrouter_key"]["detail"].startswith("PHANROUTER_API_KEY sd2.5 cloud.phanthy.com:443")
    assert rows["image:phanrouter_key"]["detail"].startswith("PHANROUTER_IMAGE_API_KEY gpt-image-2 cloud.phanthy.com:443")


def test_one_dead_text_instance_is_singled_out(tmp_path):
    routes = {**alive_routes(), ("GET", "127.0.0.1:18121", "/v1/models"): "down"}
    report = doctor.run_doctor(tmp_path, client=client_for(routes),
                               environ=base_env(make_model_dir(tmp_path)))
    rows = by_name(report)
    assert rows["text:qwen38_local[1]"]["ok"] is False
    assert rows["text:qwen38_local[0]"]["ok"] is True
    assert report["ok"] is False


def test_a_listing_answer_does_not_count_as_text_liveness(tmp_path):
    """An endpoint answering /models with an error is down, whatever it listed."""
    routes = {**alive_routes(), ("GET", "172.28.4.81:8038", "/v1/models"): 503}
    report = doctor.run_doctor(tmp_path, client=client_for(routes),
                               environ=base_env(make_model_dir(tmp_path)))
    assert by_name(report)["text:flashnext"]["ok"] is False
    assert report["ok"] is False


def test_phanrouter_key_refusal_is_not_a_validation_refusal(tmp_path):
    routes = {**alive_routes(),
              ("POST", "cloud.phanthy.com:443", "/phanrouter/api/v3/contents/generations/tasks"): 403}
    report = doctor.run_doctor(tmp_path, client=client_for(routes),
                               environ=base_env(make_model_dir(tmp_path)))
    rows = by_name(report)
    assert rows["video:phanrouter_key"]["ok"] is False
    assert "key refused" in rows["video:phanrouter_key"]["detail"]
    assert rows["image:phanrouter_key"]["ok"] is True
    assert report["ok"] is False


def test_a_missing_key_is_reported_without_a_call(tmp_path):
    env = base_env(make_model_dir(tmp_path))
    env.pop("PHANROUTER_IMAGE_API_KEY")
    env.pop("PHANROUTER_API_KEY")
    report = doctor.run_doctor(tmp_path, client=client_for(alive_routes()), environ=env)
    rows = by_name(report)
    assert rows["video:phanrouter_key"]["ok"] is False
    assert "key not set" in rows["video:phanrouter_key"]["detail"]


def test_asset_library_is_probed_only_when_enabled(tmp_path):
    model_dir = make_model_dir(tmp_path)
    report = doctor.run_doctor(tmp_path, client=client_for(alive_routes()), environ=base_env(model_dir))
    assert "assets:phanrouter_library" not in by_name(report)

    env = base_env(model_dir, PHANROUTER_REFERENCE_ASSETS="1", PHANROUTER_ASSET_GROUP_ID="grp")
    routes = {**alive_routes(), ("HEAD", "cloud.phanthy.com:443", "/phanrouter/open/CreateAsset"): 405}
    report = doctor.run_doctor(tmp_path, client=client_for(routes), environ=env)
    assert by_name(report)["assets:phanrouter_library"]["ok"] is True

    env = base_env(model_dir, PHANROUTER_REFERENCE_ASSETS="1")
    report = doctor.run_doctor(tmp_path, client=client_for(alive_routes()), environ=env)
    row = by_name(report)["assets:phanrouter_library"]
    assert row["ok"] is False and row["detail"] == "PHANROUTER_ASSET_GROUP_ID not set"


def test_local_image_service_is_checked_only_when_configured(tmp_path):
    model_dir = make_model_dir(tmp_path)
    report = doctor.run_doctor(tmp_path, client=client_for(alive_routes()), environ=base_env(model_dir))
    assert "image:local_qwen" not in by_name(report)

    env = base_env(model_dir, NOVEL_LOCAL_QWEN_IMAGE_URL="http://127.0.0.1:19300")
    routes = {**alive_routes(), ("GET", "127.0.0.1:19300", "/health"): 200}
    report = doctor.run_doctor(tmp_path, client=client_for(routes), environ=env)
    assert by_name(report)["image:local_qwen"]["ok"] is True

    routes = {**alive_routes(), ("GET", "127.0.0.1:19300", "/health"): "down"}
    report = doctor.run_doctor(tmp_path, client=client_for(routes), environ=env)
    assert by_name(report)["image:local_qwen"]["ok"] is False


def test_asr_model_dir_must_hold_the_loaded_files(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    report = doctor.run_doctor(tmp_path, client=client_for(alive_routes()), environ=base_env(empty))
    row = by_name(report)["asr:model_dir"]
    assert row["ok"] is False and "model.int8.onnx" in row["detail"]

    env = base_env(tmp_path / "sensevoice")
    env.pop("NOVEL_SENSEVOICE_MODEL_DIR")
    report = doctor.run_doctor(tmp_path, client=client_for(alive_routes()), environ=env)
    row = by_name(report)["asr:model_dir"]
    assert row["ok"] is False and row["detail"] == "NOVEL_SENSEVOICE_MODEL_DIR not set"


def test_deprecated_env_names_are_flagged_without_blocking(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_PROJECT_ROOT", str(tmp_path))
    (tmp_path / ".env").write_text(
        "NOVEL_TTS_COMMAND=fake-old-tts-value\n"
        "PHANROUTER_SD25_KEY=fake-old-key-value\n"
        "PHANROUTER_API_KEY=placeholder\n"
        "# NOVEL_VOICE_MAP_JSON=commented-out stays quiet\n",
        encoding="utf-8")
    report = doctor.run_doctor(tmp_path, client=client_for(alive_routes()),
                               environ=base_env(make_model_dir(tmp_path)))
    row = by_name(report)["env:manifest"]
    assert row["ok"] is False
    assert "NOVEL_TTS_COMMAND" in row["detail"] and "PHANROUTER_SD25_KEY" in row["detail"]
    assert "NOVEL_VOICE_MAP_JSON" not in row["detail"]  # commented lines are not set
    assert "fake-old-tts-value" not in row["detail"]  # values are never reported
    assert "fake-old-key-value" not in row["detail"]
    assert report["ok"] is True  # advisory: deprecated names never block


def test_env_manifest_stays_quiet_without_deprecated_names(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_PROJECT_ROOT", str(tmp_path))
    (tmp_path / ".env").write_text("PHANROUTER_API_KEY=placeholder\n", encoding="utf-8")
    report = doctor.run_doctor(tmp_path, client=client_for(alive_routes()),
                               environ=base_env(make_model_dir(tmp_path)))
    row = by_name(report)["env:manifest"]
    assert row["ok"] is True and "deprecated" not in row["detail"]
    assert "placeholder" not in row["detail"]


def test_dispatch_accepts_the_doctor_action(tmp_path, monkeypatch):
    run = tmp_path / "run"
    run.mkdir()
    episode_dir = tmp_path / "book" / "book_1"
    episode_dir.mkdir(parents=True)
    ctx = SimpleNamespace(novel_dir=tmp_path / 'book', run_dir=run, episode_dir=episode_dir)
    calls = []

    def fake_run_doctor(novel_dir):
        calls.append(novel_dir)
        return {"ok": False, "checks": [{"name": "text:flashnext", "ok": False, "detail": "flashnext 172.28.4.81:8038 ConnectError"}]}

    monkeypatch.setattr(doctor, "run_doctor", fake_run_doctor)
    result = EpisodeTools(ctx).dispatch("doctor")
    assert result["action"] == "doctor" and result["ok"] is True
    assert result["report"]["ok"] is False
    assert calls == [ctx.novel_dir]
    record = read_json(run / "execution.json")
    assert record["phase"] == "doctor" and record["ok"] is False


def test_asset_library_404_is_not_ready(tmp_path):
    env = base_env(make_model_dir(tmp_path), PHANROUTER_REFERENCE_ASSETS="1", PHANROUTER_ASSET_GROUP_ID="grp")
    routes = {**alive_routes(), ("HEAD", "cloud.phanthy.com:443", "/phanrouter/open/CreateAsset"): 404}
    assert by_name(doctor.run_doctor(tmp_path, client=client_for(routes), environ=env))["assets:phanrouter_library"]["ok"] is False


def test_active_planner_configuration_is_not_deprecated(tmp_path):
    path = tmp_path/'.env'
    path.write_text('NOVEL_LLM_BASE_URL=http://127.0.0.1:11434/v1\nNOVEL_LLM_API_KEY=fake-test-key\nNOVEL_LLM_MODEL=local-model\n')
    assert doctor._env_manifest_check(path)['ok'] is True


def test_example_values_match_the_projects_dotenv_readers(tmp_path, monkeypatch):
    from pathlib import Path
    from novel_manga.application.configuration import environment
    from novel_manga.application.production.conductor_common import load_dotenv as load_conductor
    from novel_manga.util import load_dotenv
    from unittest.mock import patch
    import os
    example = Path(__file__).parents[1]/'.env.example'
    path = tmp_path/'.env';path.write_text(example.read_text())
    values = environment(tmp_path, base={})
    expected = {'PHANROUTER_BASE_URL':'https://cloud.phanthy.com/phanrouter',
                'PHANROUTER_API_KEY':'','PHANROUTER_VIDEO_MODEL':'sd2.5',
                'NOVEL_PROVIDER':'phanrouter','NOVEL_ASR_COMMAND':''}
    assert {k:values[k] for k in expected} == expected
    for loader in (load_dotenv, load_conductor):
        with patch.dict(os.environ, {}, clear=True):
            loader(path)
            assert {k:os.environ[k] for k in expected} == expected
