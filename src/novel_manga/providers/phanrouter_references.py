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
        if (getattr(self.settings, 'video_model', '') == 'MiniMax-H3'
                and getattr(self.settings, 'phanrouter_asset_public_base', None)
                and not self.settings.inline_reference_images):
            return self._published_image_url(image.path)
        if getattr(self.settings, "reference_images_via_assets", False) and not self.settings.inline_reference_images:
            return self._asset_reference(image)
        return self._hosted_image_url(image)


    def _published_image_url(self, path: Path) -> str:
        """Cloud H3 needs HTTP references even for local cards/crops without image tasks."""
        from urllib.parse import quote
        from ..media.publish import publish_file, publish_root
        root = publish_root(getattr(self.settings, 'output_root', None))
        published = publish_file(path, root, reference=True)
        # publish_file already computed the existing content-keyed layout.
        # Build from its returned path rather than reading/hashing the card again.
        relative = '/'.join(quote(part, safe='') for part in published.relative_to(root).parts)
        url = self.settings.phanrouter_asset_public_base.rstrip('/') + '/' + relative
        try:
            probe = self.client.head(url, timeout=min(self.settings.request_timeout, SUBMIT_TIMEOUT_SECONDS),
                                     headers={'ngrok-skip-browser-warning': '1'}, follow_redirects=True)
        except httpx.HTTPError as error:
            raise RuntimeError(f'{path.parent.name}/{path.name}: public reference probe failed '
                               f'({type(error).__name__}); check PHANROUTER_ASSET_PUBLIC_BASE') from None
        if probe.status_code != 200:
            raise RuntimeError(f'{path.parent.name}/{path.name}: public reference probe HTTP {probe.status_code}; '
                               'check PHANROUTER_ASSET_PUBLIC_BASE and its publish directory')
        return url

    def public_card_url(self, path: Path, digest: str) -> str:
        """Where publish_cards.sh puts a copy of this card: <base>/<novel>/<asset>/<view>-<sha12><ext>."""
        base = getattr(self.settings, "phanrouter_asset_public_base", None) or ""
        novel = path.parents[3].name if len(path.parents) > 3 else "novel"
        from urllib.parse import quote
        # Edition names carry non-ASCII (zhiyao episodes, 2026-10-23): the gateway
        # downloads the URL verbatim, so every path segment must be percent-encoded
        # or the library side answers InvalidParameter.DownloadFailed.
        segments = "/".join(quote(part) for part in (novel, path.parent.name))
        return f"{base}/{segments}/{quote(path.stem)}-{digest[:12]}{path.suffix}"


    def _asset_base_url(self) -> str:
        # The library lives beside the API root: https://host/phanrouter/open/CreateAsset, the tasks under /api/v3.
        base = self.settings.phanrouter_base_url.rstrip("/")
        return base[: -len("/api")] if base.endswith("/api") else base


    def _asset_reference(self, image: ImageResult, hosted_url: str | None = None) -> str:
        # Clip workers regularly share one character card. Serialize the existing
        # sidecar so parallel submissions cannot register/pay for it repeatedly.
        import fcntl
        if not image.path.is_file():
            raise FileNotFoundError(image.path)
        sidecar = image.path.with_suffix(image.path.suffix + ASSET_SIDECAR)
        with sidecar.with_suffix(sidecar.suffix+'.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return self._asset_reference_locked(image, hosted_url)

    def _asset_reference_locked(self, image: ImageResult, hosted_url: str | None = None) -> str:
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
            if getattr(self.settings, 'video_model', '') in {'sd2.0-fast', 'sd2.0-mini'}:
                self._await_active(sidecar, record)
            return f"asset://{record['asset_id']}"
        if getattr(self.settings, "phanrouter_asset_public_base", None):
            hosted_url = self.public_card_url(path, digest)
            # publish_cards.sh was never in this repo; the copy step lives here now.  Write it,
            # then probe: the probe is what tells you the tunnel actually serves the publish dir.
            try:
                from ..media.publish import publish_file, publish_root
                published = publish_file(path, publish_root(getattr(self.settings, "output_root", None)), reference=True)
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
        prefix = f"{novel}-{path.parent.name}-{path.stem}"
        new_sd = getattr(self.settings, 'video_model', '') in {'sd2.0-fast', 'sd2.0-mini'}
        stem = f"{prefix[:49] if new_sd else prefix}-{digest[:10]}"
        asset_id = None
        for attempt in range(3):
            name = stem if attempt == 0 else f"{stem}-{attempt + 1}"
            headers = self.image_headers if new_sd else self.video_headers
            if new_sd:
                from . import media_quota
                media_quota.check(self.settings.video_model, headers.get('Authorization', '').removeprefix('Bearer '))
            response = self.client.post(
                f"{self._asset_base_url()}/open/CreateAsset", headers=headers,
                json={"GroupId": group, "Name": name, "URL": hosted_url, "AssetType": "Image"},
                timeout=min(self.settings.request_timeout, SUBMIT_TIMEOUT_SECONDS),
            )
            if response.status_code == 400 and "already exists" in response.text:
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                if new_sd:
                    try:
                        body = response.json()
                    except ValueError:
                        body = None
                    media_quota.remember(self.settings.video_model,
                        headers.get('Authorization', '').removeprefix('Bearer '), response.status_code, body)
                detail = response.text.strip().replace("\n", " ")[:600]
                raise RuntimeError(f"asset library refused {path.parent.name}/{path.name}: HTTP {response.status_code}: {detail}") from error
            asset_id = task_data(response.json()).get("Id")
            break
        if not asset_id:
            raise RuntimeError(f"asset library returned no asset id for {path.parent.name}/{path.name}")
        record = {"asset_id": asset_id, "sha256": digest, "group_id": group, "name": name,
                  "source_url": hosted_url, "created_at": time.time()}
        atomic_write_json(sidecar, record)
        if new_sd:
            self._await_active(sidecar, record)
        return f"asset://{asset_id}"


    def _await_active(self, sidecar: Path, record: dict) -> None:
        """A registered ID cannot enter a video request before processing ends."""
        if record.get('status') == 'Active':
            return
        deadline = time.monotonic() + self.settings.poll_timeout
        while time.monotonic() < deadline:
            response = self.client.post(f'{self._asset_base_url()}/open/GetAsset',
                headers=self.image_headers, json={'Id': record['asset_id']},
                timeout=min(self.settings.request_timeout, SUBMIT_TIMEOUT_SECONDS))
            response.raise_for_status()
            data = task_data(response.json())
            status = str(data.get('Status') or '')
            if not status:
                raise RuntimeError(f'asset library returned no processing status for {sidecar.parent.name}')
            record.update(status=status, status_checked_at=time.time())
            atomic_write_json(sidecar, record)
            if status == 'Active':
                return
            if status.lower() in {'failed', 'rejected', 'deleted', 'disabled', 'error'}:
                raise RuntimeError(f'asset library processing {status} for {sidecar.parent.name}')
            time.sleep(5)
        raise TimeoutError(f'asset library is still processing {sidecar.parent.name}; resume its existing ID')

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


