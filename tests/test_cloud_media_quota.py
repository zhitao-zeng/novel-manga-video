"""Confirmed quota stops follow the actual key across cloud-video channels."""
import json
from pathlib import Path

import httpx
import pytest

from novel_manga.config import Settings
from novel_manga.providers import media_quota
from novel_manga.providers.phanrouter import PhanRouterMediaProvider


def test_image_stop_is_shared_with_cloud_h3_fast_mini_but_not_another_key():
    assert media_quota.remember('gpt-image-2', 'shared-test', 403, {'error': {'code': 'quota_not_enough'}})
    for model in ('MiniMax-H3', 'sd2.0-fast', 'sd2.0-mini'):
        with pytest.raises(RuntimeError, match='quota_not_enough'):
            media_quota.check(model, 'shared-test')
        media_quota.check(model, 'rotated-test')
    media_quota.check('sd2.5', 'separate-video-test')
    assert 'shared-test' not in media_quota.marker('MiniMax-H3', 'shared-test').read_text()


@pytest.mark.parametrize('status,body', [(403, {'error': {'code': 'permission_denied'}}),
    (429, {'error': {'code': 'rate_limit'}}), (500, {'error': {'code': 'quota_not_enough'}}),
    (402, {'error': {'code': 'insufficient_credits', 'available_points': 50}}),
    (402, 'not a structured error')])
def test_auth_rate_limit_and_server_errors_do_not_become_quota_stops(status, body):
    assert not media_quota.remember('MiniMax-H3', 'test-only', status, body)
    media_quota.check('sd2.0-fast', 'test-only')


def test_video_quota_rejection_stops_the_other_channel_before_a_second_post(tmp_path, monkeypatch):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(403, json={'error': {'code': 'quota_not_enough'}})
    for model in ('sd2.0-fast', 'MiniMax-H3'):
        provider = PhanRouterMediaProvider(Settings(video_model=model, phanrouter_api_key='test-shared'))
        provider.client.close()
        provider.client = httpx.Client(transport=httpx.MockTransport(handle))
        try:
            with pytest.raises(RuntimeError):
                provider.create_video('Approved.', None, tmp_path/(model+'.mp4'), 6)
        finally:
            provider.client.close()
    assert len(calls) == 1


def test_confirmed_quota_does_not_prevent_existing_task_poll_and_download(tmp_path):
    payloads = []
    def handle(request):
        if request.method == 'POST':
            payloads.append(json.loads(request.content))
            return httpx.Response(200, json={'task_id': 'prior-job'})
        if request.url.host == 'cdn.invalid':
            return httpx.Response(200, content=b'accepted original video')
        return httpx.Response(200, json={'data': {'status': 'succeeded', 'url': 'https://cdn.invalid/clip.mp4'}})
    provider = PhanRouterMediaProvider(Settings(video_model='MiniMax-H3', phanrouter_api_key='test-only'))
    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(handle))
    output = tmp_path/'clip.mp4'
    try:
        provider.create_video('Original.', None, output, 6)
        media_quota.remember('gpt-image-2', 'test-only', 403, {'error': {'code': 'quota_not_enough'}})
        output.unlink()
        provider.create_video('Original.', None, output, 6)
        assert output.read_bytes() == b'accepted original video'
        assert len(payloads) == 1
    finally:
        provider.client.close()


def test_an_expired_shared_stop_does_not_create_a_permanent_lockout():
    media_quota.remember('MiniMax-H3', 'test-only', 402,
                         {'error': {'code': 'insufficient_quota', 'resets_at_epoch': 1}})
    media_quota.check('sd2.0-mini', 'test-only')
