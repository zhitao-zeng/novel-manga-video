"""Local H3 provider and pool fixtures backed entirely by MockTransport."""
import json
import types
from pathlib import Path
import httpx
from PIL import Image
from novel_manga.providers import h3_pool
from novel_manga.providers.h3_pool import H3Pool
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
