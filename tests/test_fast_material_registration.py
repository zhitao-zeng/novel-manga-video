"""Parallel cloud clip workers share one approved material registration."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json
import time

import httpx
from PIL import Image

from novel_manga.config import Settings
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter_references import ReferenceMaterials
from novel_manga.util import read_json


def reference_case(tmp_path, handle):
    path = tmp_path/'card.jpeg'
    Image.new('RGB', (32, 48)).save(path)
    client = httpx.Client(transport=httpx.MockTransport(handle))
    refs = ReferenceMaterials(Settings(video_model='sd2.0-fast', reference_images_via_assets=True,
        phanrouter_asset_group_id='image-owned-group'), client,
        {'Authorization': 'Bearer video-test'}, {'Authorization': 'Bearer image-test'})
    return refs, ImageResult(path, public_url='https://cdn.invalid/card.jpeg'), client


def test_fast_material_uses_image_key_and_waits_until_the_registered_id_is_active(tmp_path):
    calls = []
    def handle(request):
        assert request.headers['Authorization'] == 'Bearer image-test'
        calls.append(request.url.path)
        if request.url.path.endswith('CreateAsset'):
            return httpx.Response(200, json={'data': {'Id': 'original-reference-id'}})
        return httpx.Response(200, json={'data': {'Status': 'Active'}})
    refs, image, client = reference_case(tmp_path, handle)
    try:
        assert refs._restore_image_url(image) == 'asset://original-reference-id'
        assert len(calls) == 2 and calls[-1].endswith('GetAsset')
        assert read_json(image.path.with_suffix('.jpeg.asset.json'))['status'] == 'Active'
    finally:
        client.close()


def test_parallel_fast_clips_register_the_same_reference_only_once(tmp_path):
    calls = []
    barrier = Barrier(4)
    def handle(request):
        if request.url.path.endswith('CreateAsset'):
            calls.append(request)
            time.sleep(.03)
            return httpx.Response(200, json={'data': {'Id': 'one-reference-id'}})
        return httpx.Response(200, json={'data': {'Status': 'Active'}})
    refs, image, client = reference_case(tmp_path, handle)
    def restore(unused):
        barrier.wait()
        return refs._restore_image_url(image)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert list(pool.map(restore, range(4))) == ['asset://one-reference-id']*4
        assert len(calls) == 1
    finally:
        client.close()
