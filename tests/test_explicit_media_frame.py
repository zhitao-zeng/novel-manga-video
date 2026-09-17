import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from novel_manga.config import Settings
from novel_manga.models import StoryBible, Character
from novel_manga.media.adapters import FramedPhanRouter
from novel_manga.media.asset_builder import FramedAssetFactory
from novel_manga.media.asset_style import AssetStyle


@pytest.mark.parametrize('model', ['image-test', 'doubao-seedream-4.5'])
def test_asset_kind_decides_request_frame_and_requests_are_isolated(tmp_path, monkeypatch, model):
    requests = []
    def handle(request):
        if request.method == 'POST':
            body = json.loads(request.content); requests.append(body)
            if model.startswith('doubao-seedream'):
                return httpx.Response(200, json={'data': [{'url': 'http://cdn.invalid/card'}]})
            return httpx.Response(200, json={'task_id': 'fixed'})
        return httpx.Response(200, json={'data': {'status': 'succeeded', 'url': 'http://cdn.invalid/card'}})
    def download(client, url, output, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True); output.write_bytes(b'fixed image')
    monkeypatch.setattr('novel_manga.providers.phanrouter_images.download_file', download)
    settings = Settings(image_model=model)
    provider = FramedPhanRouter(settings, {'image_ratio': '16:9', 'video_ratio': '16:9'}, resolution='480p')
    provider.client.close()
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider.client = client
        factory = FramedAssetFactory(settings, provider, style=AssetStyle(frame_text='横屏16:9'))
        bible = StoryBible(novel_title='测试', genre='generic', visual_style='二维国漫', palette='青',
                           style_fingerprint='fixed', characters=[Character(name='甲', appearance='青衣', wardrobe='青衣')],
                           locations=['庭院'])
        factory.build_selected(tmp_path / 'series_assets', bible, {'character_001'}, {'location_001'}, expressions=False)
        key, portrait, landscape = ('size', '1080x1920', '1920x1080') if model.startswith('doubao-seedream') else ('aspectRatio', '9:16', '16:9')
        assert [r[key] for r in requests] == [portrait, landscape]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda pair: provider.create_image(pair[0], tmp_path / f'{pair[0]}.jpeg', aspect_ratio=pair[1]),
                          [('portrait', '9:16'), ('landscape', '16:9')]))
        assert {r['prompt']: r[key] for r in requests[-2:]} == {'portrait': portrait, 'landscape': landscape}
        assert client.post.__self__ is client
    payload = provider._video_payload('scene', None, 15)
    assert (payload['ratio'], payload['resolution']) == ('16:9', '480p')


def test_fixed_model_resolution_still_takes_precedence():
    provider = FramedPhanRouter(Settings(video_model='MiniMax-H3'), {'video_ratio': '16:9'}, resolution='480p')
    try:
        payload = provider._video_payload('scene', None, 30)
        assert (payload['resolution'], payload['duration']) == ('768P', 15)
    finally:
        provider.client.close()
