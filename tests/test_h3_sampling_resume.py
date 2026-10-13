"""Changing the default sampler must preserve a provably identical in-flight H3 task."""
from __future__ import annotations

import hashlib
import json
import math
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from novel_manga.providers import local_h3
from novel_manga.providers.base import ImageResult
from novel_manga.providers.local_h3 import LocalH3MediaProvider
from novel_manga.providers.phanrouter_tasks import SubmissionUncertain
from novel_manga.providers.phanrouter_video import audio_data_url


BASE_URL = "http://h3.invalid"
SAMPLING_544 = {
    "short_edge": 544,
    "aspect_ratio": "16:9",
    "num_inference_steps": 9,
    "flow_shift": 12.0,
    "audio_flow_shift": 3.0,
}
SAMPLING_768 = {**SAMPLING_544, "short_edge": 768}


@pytest.fixture
def make_provider(monkeypatch):
    monkeypatch.setenv("NOVEL_H3_MARK_DIALOGUE", "0")
    monkeypatch.setattr(local_h3, "POLL_SECONDS", 0)
    clients = []

    def make(handler, ratio="16:9"):
        provider = object.__new__(LocalH3MediaProvider)
        provider.settings = SimpleNamespace(
            video_model="minimax-h3-ref2va-turbo", request_timeout=30.0, poll_timeout=5.0,
        )
        provider.pool = None
        provider.base_url = BASE_URL
        provider.ratio = ratio
        provider.h3_client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
        clients.append(provider.h3_client)
        return provider

    yield make
    for client in clients:
        client.close()


@pytest.fixture
def clip_request(tmp_path):
    primary = tmp_path / "primary.png"
    extra = tmp_path / "location.png"
    Image.new("RGB", (8, 12), "navy").save(primary)
    Image.new("RGB", (12, 8), "green").save(extra)
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFF-fixed-reference-audio")
    return {
        "prompt": "@图片1站在@图片2门边，@音频1说：{谁在那里？}",
        "image": ImageResult(path=primary),
        "output": tmp_path / "clip.mp4",
        "duration": 5.2,
        "additional_images": (extra,),
        "reference_audios": (audio,),
        "seed_variant": 0,
    }


def original_payload(provider, request, *, short_edge=768, num_inference_steps=9):
    """Freeze the historical request shape without relying on today's default _payload."""
    seconds = max(4, min(15, math.ceil(request["duration"])))
    images = [request["image"].path, *request["additional_images"]]
    conditions = [
        {"type": "image", "role": "reference", "uri": provider._image_data_url(path)}
        for path in images
    ]
    conditions.extend(
        {"type": "audio", "role": "reference", "uri": audio_data_url(path)}
        for path in request["reference_audios"]
    )
    return {
        "model": "minimax-h3-ref2va-turbo",
        "task": "ref2va",
        "prompt": "<Picture 1>站在<Picture 2>门边，<Audio 1>说：{谁在那里？}",
        "seconds": seconds,
        "conditions": conditions,
        "target": {
            "short_edge": short_edge,
            "aspect_ratio": provider.ratio,
            "duration_seconds": float(seconds),
        },
        "num_outputs_per_prompt": 1,
        "num_inference_steps": num_inference_steps,
        "flow_shift": 12.0,
        "audio_flow_shift": 3.0,
    }


def request_digest(payload, seed_variant=0):
    unsigned = {key: value for key, value in payload.items() if key != "seed"}
    if seed_variant:
        unsigned["seed_variant"] = seed_variant
    return hashlib.sha256(
        json.dumps(unsigned, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def task_path(request):
    output = request["output"]
    return output.with_suffix(output.suffix + ".task.json")


def write_old_task(provider, request, *, sampling=True, num_inference_steps=9):
    digest = request_digest(
        original_payload(provider, request, num_inference_steps=num_inference_steps),
        request["seed_variant"],
    )
    record = {
        "task_id": "pending-768",
        "kind": "video",
        "model": provider.settings.video_model,
        "endpoint": f"{BASE_URL}/v1/videos",
        "local": True,
        "output_format": "mp4",
        "seconds": 6,
        "request_sha256": digest,
        "seed": int(digest[:8], 16) % (2 ** 31),
    }
    if sampling:
        record["h3_sampling"] = dict(SAMPLING_768)
    task_path(request).write_text(json.dumps(record), encoding="utf-8")
    return record


def sampling_of(payload):
    return {
        "short_edge": payload["target"]["short_edge"],
        "aspect_ratio": payload["target"]["aspect_ratio"],
        "num_inference_steps": payload["num_inference_steps"],
        "flow_shift": payload["flow_shift"],
        "audio_flow_shift": payload["audio_flow_shift"],
    }


def completed_handler(calls, payloads):
    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "fresh-544"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"generated-mp4")
        return httpx.Response(200, json={"status": "completed"})

    return handle


@pytest.mark.parametrize("ratio", ["16:9", "9:16"])
def test_sampling_parameters_and_fresh_post_record_the_actual_544_sampler(
    make_provider, clip_request, ratio,
):
    calls, payloads = [], []
    provider = make_provider(completed_handler(calls, payloads), ratio=ratio)
    expected_sampling = {**SAMPLING_544, "aspect_ratio": ratio}
    assert provider.sampling_parameters() == expected_sampling

    assert provider.create_video(**clip_request) == clip_request["output"]
    assert clip_request["output"].read_bytes() == b"generated-mp4"
    payload, = payloads
    record = json.loads(task_path(clip_request).read_text(encoding="utf-8"))
    assert sampling_of(payload) == expected_sampling
    assert record["h3_sampling"] == expected_sampling
    assert record["request_sha256"] == request_digest(payload)
    assert record["seed"] == payload["seed"] == int(record["request_sha256"][:8], 16) % (2 ** 31)
    assert calls == [
        ("POST", "/v1/videos"),
        ("GET", "/v1/videos/fresh-544"),
        ("GET", "/v1/videos/fresh-544/content"),
    ]


@pytest.mark.parametrize("sampling", [True, False], ids=["explicit-sampling", "legacy-digest"])
@pytest.mark.parametrize("seed_variant", [0, 3])
def test_pending_768_task_resumes_without_post_and_retains_its_identity(
    make_provider, clip_request, sampling, seed_variant,
):
    calls, payloads = [], []
    states = iter(["queued", "completed"])

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "unexpected-new-task"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"existing-768-mp4")
        return httpx.Response(200, json={"status": next(states)})

    provider = make_provider(handle)
    clip_request["seed_variant"] = seed_variant
    old_record = write_old_task(provider, clip_request, sampling=sampling)
    fresh_payload = provider._payload(
        clip_request["prompt"],
        [clip_request["image"].path, *clip_request["additional_images"]],
        clip_request["reference_audios"],
        clip_request["duration"],
    )
    assert old_record["request_sha256"] != request_digest(fresh_payload, seed_variant)

    provider.create_video(**clip_request)

    assert payloads == []
    assert calls == [
        ("GET", "/v1/videos/pending-768"),
        ("GET", "/v1/videos/pending-768"),
        ("GET", "/v1/videos/pending-768/content"),
    ]
    assert clip_request["output"].read_bytes() == b"existing-768-mp4"
    record = json.loads(task_path(clip_request).read_text(encoding="utf-8"))
    assert record["task_id"] == old_record["task_id"]
    assert record["seed"] == old_record["seed"]
    assert record["request_sha256"] == old_record["request_sha256"]
    assert record["h3_sampling"] == SAMPLING_768
    assert not task_path(clip_request).with_suffix(".stale.json").exists()


@pytest.mark.parametrize("sampling", [True, False], ids=["explicit-sampling", "legacy-digest"])
@pytest.mark.parametrize("changed", ["prompt", "primary", "additional", "audio", "duration", "seed_variant"])
@pytest.mark.parametrize("completed", [False, True])
def test_changed_clip_cannot_resume_an_old_768_task(make_provider, clip_request, sampling, changed, completed):
    calls, payloads = [], []
    provider = make_provider(completed_handler(calls, payloads))
    clip_request["seed_variant"] = 3
    old_record = write_old_task(provider, clip_request, sampling=sampling)
    if changed == "prompt":
        clip_request["prompt"] += " 门打开。"
    elif changed == "primary":
        Image.new("RGB", (8, 12), "red").save(clip_request["image"].path)
    elif changed == "additional":
        Image.new("RGB", (12, 8), "yellow").save(clip_request["additional_images"][0])
    elif changed == "audio":
        clip_request["reference_audios"][0].write_bytes(b"RIFF-changed-reference-audio")
    elif changed == "duration":
        clip_request["duration"] = 6.2
    else:
        clip_request["seed_variant"] = 4

    if not completed:
        original_receipt = task_path(clip_request).read_bytes()
        with pytest.raises(SubmissionUncertain):
            provider.create_video(**clip_request)
        assert calls == [] and payloads == []
        assert task_path(clip_request).read_bytes() == original_receipt
        return
    clip_request['output'].write_bytes(b'previous completed video')
    task_path(clip_request).write_text(json.dumps({**old_record, 'client_timing': {'completed_observed_at': 1}}))
    provider.create_video(**clip_request)

    payload, = payloads
    assert sampling_of(payload) == SAMPLING_544
    assert all("pending-768" not in path for _, path in calls)
    record = json.loads(task_path(clip_request).read_text(encoding="utf-8"))
    assert record["task_id"] == "fresh-544"
    assert record["h3_sampling"] == SAMPLING_544
    assert record["request_sha256"] != old_record["request_sha256"]
    assert record["request_sha256"] == request_digest(payload, clip_request["seed_variant"])
    stale = json.loads(task_path(clip_request).with_suffix(".stale.json").read_text(encoding="utf-8"))
    assert stale["request_sha256"] == old_record["request_sha256"]


def test_legacy_seven_evaluation_request_is_not_guessed_from_its_digest(make_provider, clip_request):
    calls, payloads = [], []
    provider = make_provider(completed_handler(calls, payloads))
    old_record = write_old_task(provider, clip_request, sampling=False, num_inference_steps=8)

    clip_request['output'].write_bytes(b'previous completed video')
    task_path(clip_request).write_text(json.dumps({**old_record, 'client_timing': {'completed_observed_at': 1}}))
    provider.create_video(**clip_request)

    payload, = payloads
    assert sampling_of(payload) == SAMPLING_544
    assert all("pending-768" not in path for _, path in calls)
    record = json.loads(task_path(clip_request).read_text(encoding="utf-8"))
    assert record["h3_sampling"] == SAMPLING_544
    assert record["request_sha256"] != old_record["request_sha256"]
    stale = json.loads(task_path(clip_request).with_suffix(".stale.json").read_text(encoding="utf-8"))
    assert stale["request_sha256"] == old_record["request_sha256"]


@pytest.mark.parametrize("unavailable", ["forgotten", "excluded"])
def test_unavailable_original_768_task_is_retained_without_new_submission(
    make_provider, clip_request, unavailable,
):
    calls, payloads = [], []
    complete = completed_handler(calls, payloads)

    def handle(request):
        if request.url.path == "/v1/videos/pending-768":
            assert unavailable == "forgotten", "an excluded instance must not be polled"
            calls.append((request.method, request.url.path))
            return httpx.Response(404)
        return complete(request)

    provider = make_provider(handle)
    old_record = write_old_task(provider, clip_request, sampling=False)
    if unavailable == "excluded":
        provider.pool = SimpleNamespace(
            excluded=lambda base: True,
            acquire=lambda **kwargs: (SimpleNamespace(url=BASE_URL, name="replacement"), None),
        )

    with pytest.raises(SubmissionUncertain):
        provider.create_video(**clip_request)
    assert payloads == []
    record = json.loads(task_path(clip_request).read_text(encoding="utf-8"))
    assert record['task_id'] == old_record['task_id']
    assert record['request_sha256'] == old_record['request_sha256']
    assert not task_path(clip_request).with_suffix('.stale.json').exists()
