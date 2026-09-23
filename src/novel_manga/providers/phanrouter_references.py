"""Register/reuse reference materials and restore their hosted or inline representations."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import httpx
from pathlib import Path
from PIL import Image, ImageOps
from ..util import atomic_write_json
from .base import ImageResult
from .phanrouter_images import poll_image_url
from .phanrouter_tasks import task_data, SUBMIT_TIMEOUT_SECONDS

ASSET_SIDECAR = '.asset.json'

class ReferenceMaterials:
    def __init__(self, settings, client, video_headers, image_headers):
        self.settings, self.client = settings, client
        self.video_headers, self.image_headers = video_headers, image_headers

    def _restore_image_url(self, image: ImageResult) -> str:
        """The reference as the video request carries it: asset://id when the asset library is on, else the hosted URL."""
        if getattr(self.settings, "reference_images_via_assets", False) and not self.settings.inline_reference_images:
            return self._asset_reference(image)
        return self._hosted_image_url(image)


    def public_card_url(self, path: Path, digest: str) -> str:
        """Where publish_cards.sh puts a copy of this card: <base>/<novel>/<asset>/<view>-<sha12><ext>."""
        base = getattr(self.settings, "phanrouter_asset_public_base", None) or ""
        novel = path.parents[3].name if len(path.parents) > 3 else "novel"
        return f"{base}/{novel}/{path.parent.name}/{path.stem}-{digest[:12]}{path.suffix}"


    def _asset_base_url(self) -> str:
        # The library lives beside the API root: https://host/phanrouter/open/CreateAsset, the tasks under /api/v3.
        base = self.settings.phanrouter_base_url.rstrip("/")
        return base[: -len("/api")] if base.endswith("/api") else base


    def _asset_reference(self, image: ImageResult, hosted_url: str | None = None) -> str:
        """asset://<id> for this image: created once in the asset library and remembered in a sidecar keyed
        by the file's content, so a redrawn card gets a new asset and an unchanged one never a second.
        The library fetches the image itself, so the source is the published copy under the public base
        when one is configured (checked with a HEAD first), else the hosted URL handed in or looked up."""
        group = getattr(self.settings, "phanrouter_asset_group_id", None)
        if not group:
            raise ValueError("PHANROUTER_REFERENCE_ASSETS is on but PHANROUTER_ASSET_GROUP_ID is not set")
        path = image.path
        if not path.is_file():
            raise FileNotFoundError(path)
        sidecar = path.with_suffix(path.suffix + ASSET_SIDECAR)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            record = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = {}
        if record.get("asset_id") and record.get("sha256") == digest and record.get("group_id") == group:
            return f"asset://{record['asset_id']}"
        if getattr(self.settings, "phanrouter_asset_public_base", None):
            hosted_url = self.public_card_url(path, digest)
            # publish_cards.sh was never in this repo; the copy step lives here now.  Write it,
            # then probe: the probe is what tells you the tunnel actually serves the publish dir.
            try:
                from ..media.publish import publish_file, publish_root
                published = publish_file(path, publish_root(getattr(self.settings, "output_root", None)))
            except OSError:
                published = None
            probe = self.client.head(hosted_url, timeout=min(self.settings.request_timeout, SUBMIT_TIMEOUT_SECONDS),
                                     headers={"ngrok-skip-browser-warning": "1"}, follow_redirects=True)
            if probe.status_code != 200:
                where = f" (the copy is at {published}; point the tunnel serving PHANROUTER_ASSET_PUBLIC_BASE at that directory - the dashboard's /published/ route serves it)" if published else ""
                raise RuntimeError(f"{path.parent.name}/{path.name} is not published at {hosted_url} (HTTP {probe.status_code});{where}")
        elif hosted_url is None:
            hosted_url = self._hosted_image_url(image)
        # Names are unique per user: novel, card, view and a piece of the content hash; a clash gets a suffix.
        novel = path.parents[3].name if len(path.parents) > 3 else "novel"
        stem = f"{novel}-{path.parent.name}-{path.stem}-{digest[:10]}"
        asset_id = None
        for attempt in range(3):
            name = stem if attempt == 0 else f"{stem}-{attempt + 1}"
            response = self.client.post(
                f"{self._asset_base_url()}/open/CreateAsset", headers=self.video_headers,
                json={"GroupId": group, "Name": name, "URL": hosted_url, "AssetType": "Image"},
                timeout=min(self.settings.request_timeout, SUBMIT_TIMEOUT_SECONDS),
            )
            if response.status_code == 400 and "already exists" in response.text:
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                detail = response.text.strip().replace("\n", " ")[:600]
                raise RuntimeError(f"asset library refused {path.parent.name}/{path.name}: HTTP {response.status_code}: {detail}") from error
            asset_id = task_data(response.json()).get("Id")
            break
        if not asset_id:
            raise RuntimeError(f"asset library returned no asset id for {path.parent.name}/{path.name}")
        atomic_write_json(sidecar, {"asset_id": asset_id, "sha256": digest, "group_id": group, "name": name,
                                    "source_url": hosted_url, "created_at": time.time()})
        return f"asset://{asset_id}"


    def _hosted_image_url(self, image: ImageResult) -> str:
        if image.public_url:
            return image.public_url
        if self.settings.inline_reference_images:
            if not image.path.is_file():
                raise FileNotFoundError(image.path)
            with Image.open(image.path) as source:
                normalized = ImageOps.exif_transpose(source).convert("RGB")
                # The whole card, long side 1280.  Fitting every reference to 720x1280 cut a 16:9 scene card
                # down to its middle third - the buildings, doors and space either side went missing.
                normalized = ImageOps.contain(
                    normalized,
                    (1280, 1280),
                    method=Image.Resampling.LANCZOS,
                )
                encoded = io.BytesIO()
                normalized.save(encoded, format="JPEG", quality=82, optimize=True)
            payload = encoded.getvalue()
            if len(payload) > 10 * 1024 * 1024:
                raise ValueError("Seedance inline reference image exceeds 10 MiB")
            return f"data:image/jpeg;base64,{base64.b64encode(payload).decode('ascii')}"
        task_path = image.path.with_suffix(image.path.suffix + ".task.json")
        if not task_path.is_file():
            raise ValueError("PhanRouter image task metadata is missing; cannot restore provider reference")
        task_id = json.loads(task_path.read_text(encoding="utf-8")).get("task_id")
        if not task_id:
            raise ValueError("PhanRouter image task metadata has no task_id")
        return poll_image_url(self.settings, self.client, self.image_headers, str(task_id))


