"""Card publishing: layout mirrors the provider's public URL byte for byte, copies are
content-keyed and atomic, and the provider auto-publishes before its pre-flight probe."""
import hashlib
import json

import pytest

from novel_manga.media.publish import publish_file, published_relpath, publish_root
from novel_manga.providers.phanrouter_references import ReferenceMaterials


def test_publish_layout_matches_the_providers_url(tmp_path):
    card = tmp_path / "outputs" / "wuyue" / "series_assets" / "characters" / "character_001"
    card.mkdir(parents=True)
    image = card / "turnaround.jpeg"
    image.write_bytes(b"\xff\xd8\xff-card-one")

    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    rel = published_relpath(image, digest)
    assert str(rel) == f"wuyue/character_001/turnaround-{digest[:12]}.jpeg"

    class S:  # the provider builds its URL from the same parts
        phanrouter_asset_public_base = "https://tunnel.example/published"
    url = ReferenceMaterials(S(), None, None, None).public_card_url(image, digest)
    assert url == f"https://tunnel.example/published/{rel}"


def test_publish_is_atomic_idempotent_and_picky(tmp_path):
    card = tmp_path / "outputs" / "wuyue" / "series_assets" / "characters" / "character_001"
    card.mkdir(parents=True)
    image = card / "turnaround.jpeg"
    image.write_bytes(b"\xff\xd8\xff-card-one")
    root = tmp_path / "published"

    first = publish_file(image, root)
    assert first.is_file() and ".tmp" not in [p.name for p in first.parent.iterdir()]
    mtime = first.stat().st_mtime_ns
    assert publish_file(image, root) == first
    assert first.stat().st_mtime_ns == mtime                    # 不变的内容不重写

    image.write_bytes(b"\xff\xd8\xff-card-redrawn")             # 重画 → 新名字
    second = publish_file(image, root)
    assert second != first and second.is_file() and first.is_file()

    with pytest.raises(FileNotFoundError):
        publish_file(card / "spec.json", root)                  # 非图片不发布
    with pytest.raises(FileNotFoundError):
        publish_file(card / "missing.jpeg", root)


def test_publish_root_prefers_the_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_ASSET_PUBLISH_DIR", str(tmp_path / "elsewhere"))
    assert publish_root(tmp_path / "outputs") == tmp_path / "elsewhere"
    monkeypatch.delenv("NOVEL_ASSET_PUBLISH_DIR")
    assert publish_root(tmp_path / "outputs") == tmp_path / "outputs" / ".published"


def test_provider_auto_publishes_before_the_probe(tmp_path, monkeypatch):
    """The dead publish_cards.sh path: the provider now writes the copy itself, then probes."""
    card = tmp_path / "outputs" / "propilot" / "series_assets" / "characters" / "character_001"
    card.mkdir(parents=True)
    image = card / "turnaround.jpeg"
    image.write_bytes(b"\xff\xd8\xff-pilot")
    publish_dir = tmp_path / "pub"
    monkeypatch.setenv("NOVEL_ASSET_PUBLISH_DIR", str(publish_dir))

    calls = []

    class FakeClient:
        def head(self, url, **kw):
            calls.append(url)
            class R:
                status_code = 200
            return R()

        def post(self, url, **kw):
            class R:
                status_code = 200
                text = ""

                def raise_for_status(self):
                    return None

                def json(self):
                    return {"Result": {"Id": "asset-1"}}
            return R()

    class S:
        phanrouter_asset_public_base = "https://tunnel.example/published"
        phanrouter_asset_group_id = "g1"
        phanrouter_base_url = "https://provider.example/api"
        request_timeout = 30
        inline_reference_images = False
        reference_images_via_assets = True
        output_root = tmp_path / "outputs"

    class Img:
        path = image

    out = ReferenceMaterials(S(), FakeClient(), {"v": "1"}, {"i": "1"})._asset_reference(Img())
    assert out == "asset://asset-1"
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    published = publish_dir / "propilot" / "character_001" / f"turnaround-{digest[:12]}.jpeg"
    assert published.is_file()                                  # 自动发布发生在探针之前
    assert calls and f"turnaround-{digest[:12]}.jpeg" in calls[0]
    assert json.loads(image.with_suffix(".jpeg.asset.json").read_text())["asset_id"] == "asset-1"
