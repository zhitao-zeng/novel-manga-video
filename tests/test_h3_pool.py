import json
import os
import time
import types
from pathlib import Path

import httpx
import pytest
from PIL import Image

from novel_manga.providers import h3_pool, local_h3
from novel_manga.providers.h3_pool import H3Pool, night_leases
from novel_manga.providers.local_h3 import LocalH3MediaProvider


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
    monkeypatch.setattr(pool, "healthy", lambda url: True)
    first, h1 = pool.acquire(timeout=1)
    second, h2 = pool.acquire(timeout=1)
    assert {first.url, second.url} == {"http://10.0.0.1:1", "http://10.0.0.2:2"}  # one slot each
    with pytest.raises(TimeoutError):
        pool.acquire(timeout=0.2)
    h3_pool.release(h1)
    pool.cool_down(first.url, 600, "test")
    with pytest.raises(TimeoutError):
        pool.acquire(timeout=0.2)  # the freed instance is cooling
    h3_pool.release(h2)
    third, h3 = pool.acquire(timeout=1)
    assert third.url == second.url
    h3_pool.release(h3)


def test_an_instance_that_does_not_answer_is_passed_over(tmp_path, monkeypatch):
    pool = H3Pool(write_pool(tmp_path))
    monkeypatch.setattr(pool, "healthy", lambda url: url.endswith(":2"))
    instance, handle = pool.acquire(timeout=1)
    assert instance.url == "http://10.0.0.2:2"
    h3_pool.release(handle)


def make_provider(tmp_path, handler, base_url="pool", **pool_overrides):
    provider = object.__new__(LocalH3MediaProvider)
    provider.settings = types.SimpleNamespace(video_model="minimax-h3-ref2va-turbo", request_timeout=30.0, poll_timeout=60.0)
    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
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
    monkeypatch.setattr(provider.pool, "healthy", lambda url: True)
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
    monkeypatch.setattr(provider.pool, "healthy", lambda url: True)
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
