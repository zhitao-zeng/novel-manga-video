"""With PHANROUTER_REFERENCE_ASSETS on, a reference image reaches the video request as asset://id: created
once in the asset library from the card's hosted URL, remembered beside the card by content hash, recreated
only when the bytes change, renamed on a name clash, and left alone when the setting is off."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from novel_manga.config import Settings
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter import PhanRouterMediaProvider


class Client:
    def __init__(self, conflicts: int = 0, head_status: int = 200):
        self.calls: list[dict] = []
        self.heads: list[str] = []
        self.conflicts = conflicts
        self.head_status = head_status
        self.n = 0

    def head(self, url, timeout=None, headers=None, follow_redirects=False):
        self.heads.append(url)
        return SimpleNamespace(status_code=self.head_status)

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self.conflicts:
            self.conflicts -= 1
            return SimpleNamespace(status_code=400, text='{"Error":"Conflict: asset name already exists"}',
                                   raise_for_status=lambda: None, json=lambda: {})
        self.n += 1
        return SimpleNamespace(status_code=200, text="", raise_for_status=lambda: None,
                               json=lambda: {"Result": {"Id": f"asset-{self.n}"}})


def provider(tmp_path: Path, **overrides) -> tuple[PhanRouterMediaProvider, Client, Path]:
    fields = {"phanrouter_api_key": "video-key", "phanrouter_asset_group_id": "group-1", "reference_images_via_assets": True}
    fields.update(overrides)
    settings = Settings(**fields)
    p = PhanRouterMediaProvider(settings)
    p.client = Client()
    card = tmp_path / "outputs" / "wuyue" / "series_assets" / "characters" / "character_001" / "turnaround.jpeg"
    card.parent.mkdir(parents=True)
    card.write_bytes(b"old face")
    return p, p.client, card


def test_reference_becomes_an_asset_created_once(tmp_path):
    p, client, card = provider(tmp_path)
    image = ImageResult(path=card, public_url="https://cdn.example/card.jpg")
    assert p._restore_image_url(image) == "asset://asset-1"
    call = client.calls[0]
    assert call["url"].endswith("/phanrouter/open/CreateAsset")
    assert call["headers"] == {"Authorization": "Bearer video-key"}
    assert call["json"]["GroupId"] == "group-1" and call["json"]["URL"] == "https://cdn.example/card.jpg"
    assert call["json"]["AssetType"] == "Image" and call["json"]["Name"].startswith("wuyue-character_001-turnaround-")
    sidecar = json.loads(card.with_suffix(".jpeg.asset.json").read_text())
    assert sidecar["asset_id"] == "asset-1" and sidecar["group_id"] == "group-1"
    # the same bytes again: no second request
    assert p._restore_image_url(image) == "asset://asset-1"
    assert len(client.calls) == 1
    # redrawn card: a new asset
    card.write_bytes(b"new face")
    assert p._restore_image_url(image) == "asset://asset-2"
    assert len(client.calls) == 2


def test_name_clash_gets_a_suffix(tmp_path):
    p, _, card = provider(tmp_path)
    p.client = Client(conflicts=1)
    assert p._restore_image_url(ImageResult(path=card, public_url="https://cdn.example/card.jpg")) == "asset://asset-1"
    assert [c["json"]["Name"][-2:] for c in p.client.calls][1] == "-2"


def test_off_by_default_and_inline_stays_inline(tmp_path):
    p, client, card = provider(tmp_path, reference_images_via_assets=False)
    assert p._restore_image_url(ImageResult(path=card, public_url="https://cdn.example/card.jpg")) == "https://cdn.example/card.jpg"
    assert client.calls == []
    p2, client2, card2 = provider(tmp_path / "b", inline_reference_images=True)
    import PIL.Image
    PIL.Image.new("RGB", (8, 8), "white").save(card2, format="JPEG")  # inline needs a real image
    assert p2._restore_image_url(ImageResult(path=card2)).startswith("data:image/jpeg;base64,")
    assert client2.calls == []


def test_public_base_uses_the_published_copy_after_a_head_check(tmp_path):
    import hashlib
    p, client, card = provider(tmp_path, phanrouter_asset_public_base="https://pub.example/novel")
    digest = hashlib.sha256(card.read_bytes()).hexdigest()
    expected = f"https://pub.example/novel/wuyue/character_001/turnaround-{digest[:12]}.jpeg"
    assert p._restore_image_url(ImageResult(path=card)) == "asset://asset-1"
    assert client.heads == [expected]
    assert client.calls[0]["json"]["URL"] == expected
    # registered: the sidecar answers, no network at all
    client.heads.clear(); client.calls.clear()
    assert p._restore_image_url(ImageResult(path=card)) == "asset://asset-1"
    assert client.heads == [] and client.calls == []


def test_unpublished_card_is_an_error_not_a_silent_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_ASSET_PUBLISH_DIR", str(tmp_path / "published"))
    p, _, card = provider(tmp_path, phanrouter_asset_public_base="https://pub.example/novel")
    p.client = Client(head_status=404)
    try:
        p._restore_image_url(ImageResult(path=card))
    except RuntimeError as error:
        assert "not published" in str(error) and "404" in str(error)
    else:
        raise AssertionError("an unpublished card must not be registered from a dead URL")
    assert p.client.calls == []
    # 自动发布先发生：副本已在发布目录，404 意味着隧道那头没指过来
    assert list((tmp_path / "published").rglob("*.jpeg"))


def test_group_id_is_required(tmp_path):
    p, _, card = provider(tmp_path, phanrouter_asset_group_id=None)
    try:
        p._restore_image_url(ImageResult(path=card, public_url="https://cdn.example/card.jpg"))
    except ValueError as error:
        assert "PHANROUTER_ASSET_GROUP_ID" in str(error)
    else:
        raise AssertionError("missing group id must be an error")
