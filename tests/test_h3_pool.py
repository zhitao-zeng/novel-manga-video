import json
import os
import time
import types
from pathlib import Path

import httpx
import pytest
from PIL import Image

from novel_manga.providers import h3_pool, local_h3
from novel_manga.providers.h3_pool import H3Pool, PoolUnavailable, night_leases
from novel_manga.providers.local_h3 import LocalH3MediaProvider


def test_eight_evaluation_lora_request_includes_terminal_sigma_point():
    provider = LocalH3MediaProvider.__new__(LocalH3MediaProvider)
    provider.settings = types.SimpleNamespace(video_model='minimax-h3-ref2va-turbo')
    provider.ratio = '16:9'
    payload = provider._payload('A person waits.', [], (), 15)
    assert payload['num_inference_steps'] == 9
    assert payload['target']['short_edge'] == 768
    assert (payload['flow_shift'], payload['audio_flow_shift']) == (12.0, 3.0)


def write_pool(tmp_path: Path, **overrides) -> Path:
    config = {
        "slots": 1,
        "slot_dir": str(tmp_path / "slots"),
        "exclude_hosts": ["10.0.0.3"],
        "stuck_minutes": 15,
        "resident": [
            {"name": "a", "url": "http://10.0.0.1:1"},
            {"name": "b", "url": "http://10.0.0.2:2"},
            {"name": "off", "url": "http://10.0.0.9:9", "enabled": False},
            {"name": "excluded", "url": "http://10.0.0.3:3"},
        ],
        "night_shift": {"state": str(tmp_path / "tick.json"), "drain_minutes": 15, "max_state_age_seconds": 600},
    }
    config.update(overrides)
    path = tmp_path / "pool.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def write_tick(tmp_path: Path, leases: list, part: str = "recovery") -> None:
    (tmp_path / "tick.json").write_text(json.dumps({"machines": [{"machine_id": "gpu81", part: {"leases": leases}}]}))


def lease(lease_id: str, phase: str, deadline: float, url: str = "http://10.0.0.81:32000") -> dict:
    return {"id": lease_id, "phase": phase, "deadline": deadline,
            "instances": [{"url": url, "unit": f"h3-night-h3-{lease_id}-0", "port": 32000}]}


def test_residents_follow_enabled_and_excluded_hosts(tmp_path):
    assert [m.url for m in H3Pool(write_pool(tmp_path)).members()] == ["http://10.0.0.1:1", "http://10.0.0.2:2"]


def test_a_night_lease_joins_only_while_active_and_before_its_drain(tmp_path):
    now = time.time()
    write_tick(tmp_path, [
        lease("a1", "active", now + 3600),
        lease("s1", "starting", now + 3600, "http://10.0.0.82:32000"),
        lease("r1", "restored", now + 3600, "http://10.0.0.83:32000"),
        lease("d1", "active", now + 600, "http://10.0.0.84:32000"),  # inside the 15 min drain
        lease("x1", "active", now + 3600, "http://10.0.0.3:32001"),  # excluded host
    ])
    night = [m for m in H3Pool(write_pool(tmp_path)).members(now) if m.source.startswith("night")]
    assert [m.url for m in night] == ["http://10.0.0.81:32000"]
    assert night[0].drain_at == pytest.approx(now + 3600 - 900)


def test_a_stale_tick_file_is_not_trusted(tmp_path):
    now = time.time()
    write_tick(tmp_path, [lease("a1", "active", now + 7200)])
    os.utime(tmp_path / "tick.json", (now - 3600, now - 3600))
    assert all(m.source == "resident" for m in H3Pool(write_pool(tmp_path)).members(now))


def test_the_newer_record_of_a_lease_wins():
    state = {"machines": [{"recovery": {"leases": [{"id": "a", "phase": "starting"}]},
                           "action": {"leases": [{"id": "a", "phase": "active"}]}}]}
    assert night_leases(state)[0]["phase"] == "active"


def test_slots_spread_clips_and_a_cooling_instance_is_skipped(tmp_path, monkeypatch):
    pool = H3Pool(write_pool(tmp_path))
    monkeypatch.setattr(pool, "problem", lambda instance: None)
    first, h1 = pool.acquire(timeout=1)
    second, h2 = pool.acquire(timeout=1)
    assert {first.url, second.url} == {"http://10.0.0.1:1", "http://10.0.0.2:2"}  # one slot each
    with pytest.raises(PoolUnavailable):
        pool.acquire(timeout=0.2)
    h3_pool.release(h1)
    pool.cool_down(first.url, 600, "test")
    with pytest.raises(PoolUnavailable):
        pool.acquire(timeout=0.2)  # the freed instance is cooling
    h3_pool.release(h2)
    third, h3 = pool.acquire(timeout=1)
    assert third.url == second.url
    h3_pool.release(h3)


def test_an_instance_that_does_not_answer_is_passed_over(tmp_path, monkeypatch):
    pool = H3Pool(write_pool(tmp_path))
    monkeypatch.setattr(pool, "problem", lambda instance: None if instance.url.endswith(":2") else "down")
    instance, handle = pool.acquire(timeout=1)
    assert instance.url == "http://10.0.0.2:2"
    h3_pool.release(handle)


def make_provider(tmp_path, handler, base_url="pool", **pool_overrides):
    provider = object.__new__(LocalH3MediaProvider)
    provider.settings = types.SimpleNamespace(video_model="minimax-h3-ref2va-turbo", request_timeout=30.0, poll_timeout=60.0)
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    provider.h3_client = provider.client
    provider.pool = H3Pool(write_pool(tmp_path, **pool_overrides)) if base_url == "pool" else None
    provider.base_url = "" if base_url == "pool" else base_url
    provider.ratio = "16:9"
    card = tmp_path / "card.png"
    Image.new("RGB", (64, 64)).save(card)
    return provider, card


def first_listed_first(monkeypatch):
    monkeypatch.setattr(h3_pool.random, "shuffle", lambda items: items.sort(key=lambda m: m.url))


def test_a_clip_moves_to_another_instance_when_its_own_disappears(tmp_path, monkeypatch):
    def handler(request):
        if request.url.host == "10.0.0.1":
            if request.method == "POST":
                return httpx.Response(200, json={"id": "t1"})
            raise httpx.ConnectError("gone", request=request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t2"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"mp4")
        return httpx.Response(200, json={"status": "completed"})

    provider, card = make_provider(tmp_path, handler)
    monkeypatch.setattr(provider.pool, "problem", lambda target: None)
    first_listed_first(monkeypatch)
    output = tmp_path / "clip.mp4"
    provider.create_video("prompt", None, output, 5.0, additional_images=(card,))
    assert output.read_bytes() == b"mp4"
    sidecar = json.loads((tmp_path / "clip.mp4.task.json").read_text(encoding="utf-8"))
    assert sidecar["endpoint"] == "http://10.0.0.2:2/v1/videos" and sidecar["instance"] == "b"
    assert provider.pool.cooling("http://10.0.0.1:1")


def test_a_clip_that_sits_too_long_is_given_to_another_instance(tmp_path, monkeypatch):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t-" + request.url.host})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"mp4")
        return httpx.Response(200, json={"status": "queued" if request.url.host == "10.0.0.1" else "completed"})

    provider, card = make_provider(tmp_path, handler, stuck_minutes=0.0001)
    monkeypatch.setattr(provider.pool, "problem", lambda target: None)
    monkeypatch.setattr(local_h3, "POLL_SECONDS", 0.01)
    first_listed_first(monkeypatch)
    output = tmp_path / "clip.mp4"
    provider.create_video("prompt", None, output, 5.0, additional_images=(card,))
    assert json.loads((tmp_path / "clip.mp4.task.json").read_text(encoding="utf-8"))["endpoint"].startswith("http://10.0.0.2:2")
    assert provider.pool.cooling("http://10.0.0.1:1")


def test_one_named_instance_still_renders_as_before(tmp_path):
    def handler(request):
        assert request.url.host == "10.0.0.7"
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t7"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"mp4")
        return httpx.Response(200, json={"status": "completed"})

    provider, card = make_provider(tmp_path, handler, base_url="http://10.0.0.7:7")
    output = tmp_path / "clip.mp4"
    provider.create_video("prompt", None, output, 5.0, additional_images=(card,))
    sidecar = json.loads((tmp_path / "clip.mp4.task.json").read_text(encoding="utf-8"))
    assert sidecar["endpoint"] == "http://10.0.0.7:7/v1/videos" and "instance" not in sidecar


@pytest.mark.parametrize('resumed', [False, True])
def test_polling_502_moves_both_new_and_resumed_tasks(tmp_path, monkeypatch, resumed):
    status = {'broken': not resumed}
    def handler(request):
        if request.method == 'POST':
            return httpx.Response(200, json={'id': request.url.host})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'mp4')
        if request.url.host == '10.0.0.1':
            return httpx.Response(502) if status['broken'] else httpx.Response(200, json={'status':'queued'})
        return httpx.Response(200, json={'status':'completed'})
    provider, card = make_provider(tmp_path, handler)
    monkeypatch.setattr(provider.pool, 'problem', lambda target: None)
    monkeypatch.setattr(local_h3, 'POLL_SECONDS', 0.01)
    first_listed_first(monkeypatch)
    output = tmp_path / 'clip.mp4'
    if resumed:
        provider.settings.poll_timeout = 0.05
        with pytest.raises(TimeoutError):
            provider.create_video('prompt', None, output, 5, additional_images=(card,))
        status['broken'] = True
    provider.settings.poll_timeout = 5
    # Public image traffic must not accidentally be used for any H3 request.
    provider.client = httpx.Client(transport=httpx.MockTransport(lambda r: pytest.fail('wrong client')))
    provider.create_video('prompt', None, output, 5, additional_images=(card,))
    assert output.read_bytes() == b'mp4'
    assert json.loads(output.with_suffix('.mp4.task.json').read_text())['endpoint'].startswith('http://10.0.0.2:2')


def test_retries_change_only_seed_without_narrating_retry_instructions(tmp_path):
    payloads = []
    def handler(request):
        if request.method == 'POST':
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={'id': f't{len(payloads)}'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'mp4')
        return httpx.Response(200, json={'status': 'completed'})
    provider, card = make_provider(tmp_path, handler, base_url='http://10.0.0.7:7')
    for i in range(3):
        provider.create_video('same scene and dialogue', None, tmp_path / f'clip{i}.mp4', 5, additional_images=(card,), seed_variant=i)
    assert len({p['seed'] for p in payloads}) == 3
    assert all({k:v for k,v in p.items() if k != 'seed'} == {k:v for k,v in payloads[0].items() if k != 'seed'} for p in payloads)


def write_tick_gpus(tmp_path, tick_time, gpus, machine="gpu03"):
    (tmp_path / "tick.json").write_text(json.dumps({"time": tick_time, "machines": [{"machine_id": machine, "inspection": {"gpus": gpus}}]}))


def gpu(index, utilization, service=None):
    return {"index": index, "utilization": utilization, "services": [f"systemd:{service}"] if service else []}


def resident(url, service):
    return h3_pool.Instance(url, url, 2, "resident", host="gpu03", service=service)


def test_a_service_that_holds_no_gpu_is_down_even_though_it_answers(tmp_path, monkeypatch):
    write_tick_gpus(tmp_path, time.time(), [gpu(0, 100.0, "zzt-h3-a100-a.service"), gpu(5, 0.0)])
    pool = H3Pool(write_pool(tmp_path))
    monkeypatch.setattr(pool, "_reachable", lambda url: True)
    b = resident("http://10.0.0.4:4", "zzt-h3-a100-b.service")
    assert pool.gpu_verdict(b) == "down"
    assert "holds no GPU" in pool.problem(b)
    assert pool.cooling(b.url)
    assert pool.problem(resident("http://10.0.0.5:5", "zzt-h3-a100-a.service")) is None


def test_gpus_idle_for_three_samples_with_jobs_waiting_is_stuck(tmp_path, monkeypatch):
    monkeypatch.setattr(h3_pool, "HEALTH_SECONDS", 0.0)
    monkeypatch.setattr(h3_pool, "MEMBERS_SECONDS", 0.0)
    pool = H3Pool(write_pool(tmp_path))
    monkeypatch.setattr(pool, "_reachable", lambda url: True)
    monkeypatch.setattr(pool, "_waiting", lambda url, older_than=0.0: 2)
    instance = resident("http://10.0.0.6:6", "zzt-h3-a100-a.service")
    start = time.time()
    seen = []
    for step in range(3):
        write_tick_gpus(tmp_path, start + 60 * step, [gpu(0, 0.0, "zzt-h3-a100-a.service")])
        seen.append(pool.problem(instance))
    assert seen[:2] == [None, None] and "idle" in seen[2]


def test_busy_gpus_or_no_waiting_jobs_or_a_stale_inspection_raise_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(h3_pool, "HEALTH_SECONDS", 0.0)
    monkeypatch.setattr(h3_pool, "MEMBERS_SECONDS", 0.0)
    pool = H3Pool(write_pool(tmp_path))
    monkeypatch.setattr(pool, "_reachable", lambda url: True)
    monkeypatch.setattr(pool, "_waiting", lambda url, older_than=0.0: 0)
    idle = resident("http://10.0.0.7:7", "zzt-h3-a100-a.service")
    start = time.time()
    for step in range(3):
        write_tick_gpus(tmp_path, start + 60 * step, [gpu(0, 0.0, "zzt-h3-a100-a.service")])
        assert pool.problem(idle) is None  # idle with an empty queue is just idle
    os.utime(tmp_path / "tick.json", (start - 3600, start - 3600))
    assert pool.gpu_verdict(resident("http://10.0.0.8:8", "zzt-h3-a100-b.service")) is None  # stale: says nothing


def test_only_a_job_older_than_the_idle_window_counts_as_waiting(tmp_path, monkeypatch):
    now = time.time()
    jobs = [{"status": "queued", "created_at": now - 20}, {"status": "queued", "created_at": now - 600},
            {"status": "completed", "created_at": now - 900}]

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"data": jobs}).encode()
    monkeypatch.setattr(h3_pool.urllib.request, "urlopen", lambda url, timeout: Response())
    pool = H3Pool(write_pool(tmp_path))
    assert pool._waiting("http://10.0.0.6:6") == 2
    assert pool._waiting("http://10.0.0.6:6", older_than=180) == 1


def test_idle_gpus_and_a_clip_just_handed_in_are_not_stuck(tmp_path, monkeypatch):
    # GPU052-A, 2026-09-11 21:33: three quiet samples while the pool had nothing for it, then a clip handed in 40 s
    # before the check - cooled for 20 minutes although it finished every clip in half a minute.
    monkeypatch.setattr(h3_pool, "HEALTH_SECONDS", 0.0)
    monkeypatch.setattr(h3_pool, "MEMBERS_SECONDS", 0.0)
    pool = H3Pool(write_pool(tmp_path))
    monkeypatch.setattr(pool, "_reachable", lambda url: True)
    monkeypatch.setattr(pool, "_waiting", lambda url, older_than=0.0: 0 if older_than >= 180 else 1)
    instance = resident("http://10.0.0.6:6", "zzt-h3-a100-a.service")
    start = time.time()
    for step in range(3):
        write_tick_gpus(tmp_path, start + 60 * step, [gpu(0, 0.0, "zzt-h3-a100-a.service")])
        assert pool.problem(instance) is None
    assert not pool.cooling(instance.url)


def test_a_clip_waits_for_the_pool_no_longer_than_its_own_time(tmp_path, monkeypatch):
    provider, card = make_provider(tmp_path, lambda request: httpx.Response(500))
    provider.settings.poll_timeout = 1.5
    monkeypatch.setattr(provider.pool, "problem", lambda target: None)
    held = [provider.pool.acquire(timeout=1) for _ in range(2)]  # both instances busy, one slot each
    started = time.monotonic()
    with pytest.raises(PoolUnavailable):  # a RuntimeError: the runner waits it out and asks again
        provider.create_video("prompt", None, tmp_path / "clip.mp4", 5.0, additional_images=(card,))
    assert time.monotonic() - started < 5
    for _, handle in held:
        h3_pool.release(handle)


def test_a_failed_task_is_set_aside_and_the_next_call_submits_again(tmp_path, monkeypatch):
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(1)
            return httpx.Response(200, json={"id": f"t{len(posts)}"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"mp4")
        failed = request.url.path.endswith("/t1")
        return httpx.Response(200, json={"status": "failed" if failed else "completed", "error": "CUDA error" if failed else None})

    provider, card = make_provider(tmp_path, handler)
    monkeypatch.setattr(provider.pool, "problem", lambda target: None)
    output = tmp_path / "clip.mp4"
    with pytest.raises(RuntimeError, match="failed"):
        provider.create_video("prompt", None, output, 5.0, additional_images=(card,))
    assert (tmp_path / "clip.mp4.task.failed.json").is_file()
    provider.create_video("prompt", None, output, 5.0, additional_images=(card,))
    assert output.read_bytes() == b"mp4" and len(posts) == 2


def test_a_resumed_task_holds_a_slot_on_its_instance(tmp_path, monkeypatch):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t1"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"mp4")
        return httpx.Response(200, json={"status": "queued"})

    provider, card = make_provider(tmp_path, handler)
    provider.settings.poll_timeout = 0.3
    monkeypatch.setattr(provider.pool, "problem", lambda target: None)
    monkeypatch.setattr(local_h3, "POLL_SECONDS", 0.01)
    first_listed_first(monkeypatch)
    output = tmp_path / "clip.mp4"
    with pytest.raises(TimeoutError):  # still queued on instance a when this call gives up
        provider.create_video("prompt", None, output, 5.0, additional_images=(card,))
    calls = []

    def state(task_id, base):
        calls.append(base)
        if len(calls) == 1:
            return {"status": "queued"}  # looked at first: still rendering, so it takes a slot of its instance
        with pytest.raises(PoolUnavailable):  # while it is polled, a has no slot left for anyone else
            provider.pool.hold(base, timeout=0.1)
        return {"status": "completed"}

    monkeypatch.setattr(provider, "_state", state)
    provider.settings.poll_timeout = 5.0
    provider.create_video("prompt", None, output, 5.0, additional_images=(card,))
    assert calls == ["http://10.0.0.1:1"] * 2 and output.read_bytes() == b"mp4"


def test_a_resumed_task_that_already_finished_is_downloaded_without_waiting_for_a_slot(tmp_path, monkeypatch):
    status = {"t1": "queued"}

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"id": "t1"})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"mp4")
        return httpx.Response(200, json={"status": status["t1"]})

    provider, card = make_provider(tmp_path, handler)
    provider.settings.poll_timeout = 0.3
    monkeypatch.setattr(provider.pool, "problem", lambda target: None)
    monkeypatch.setattr(local_h3, "POLL_SECONDS", 0.01)
    first_listed_first(monkeypatch)
    output = tmp_path / "clip.mp4"
    with pytest.raises(TimeoutError):  # still queued when this call gives up; the task stays on instance a
        provider.create_video("prompt", None, output, 5.0, additional_images=(card,))
    status["t1"] = "completed"  # it finished while nobody was polling (a download cut short, say)
    busy = provider.pool.hold("http://10.0.0.1:1", timeout=1)  # and every slot of its instance is taken
    provider.settings.poll_timeout = 30.0
    started = time.monotonic()
    provider.create_video("prompt", None, output, 5.0, additional_images=(card,))
    assert output.read_bytes() == b"mp4" and time.monotonic() - started < 5
    h3_pool.release(busy)
