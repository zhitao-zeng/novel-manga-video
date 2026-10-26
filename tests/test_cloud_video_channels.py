"""Opt-in cloud channels keep model, credentials and repair grammar together."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from PIL import Image

from novel_manga.config import Settings
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.providers.phanrouter_video import video_payload
from novel_manga.util import atomic_write_json, read_json


CHANNELS = [('h3cloud', 'MiniMax-H3', 'PHANROUTER_H3CLOUD_API_KEY'),
            ('sd20-fast', 'sd2.0-fast', 'PHANROUTER_IMAGE_API_KEY'),
            ('sd20-mini', 'sd2.0-mini', 'PHANROUTER_IMAGE_API_KEY')]
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('channel,model,key_var', CHANNELS)
def test_registered_cloud_resources_do_not_change_existing_novels(channel, model, key_var):
    pipeline = read_json(ROOT/'configs/pipeline.json')
    row = pipeline['resources']['video_keys'][channel]
    assert row['model'] == model and row['key_var'] == key_var
    assert not row.get('base_url') and row['clip_cap'] == 15
    assert all(channel not in n['render_keys'] for n in pipeline['novels'])


@pytest.mark.parametrize('channel,model,key_var', CHANNELS)
def test_task_route_uses_the_selected_cloud_channel_instead_of_local_h3(tmp_path, monkeypatch, channel, model, key_var):
    from novel_manga.application.resolved_settings import resolve_agent_environment
    from novel_manga.llm.config import ENDPOINTS
    atomic_write_json(tmp_path/'configs/pipeline.json', read_json(ROOT/'configs/pipeline.json'))
    ctx = SimpleNamespace(project_root=tmp_path, run_dir=tmp_path/'run', novel_dir=tmp_path/'book',
                          episode_dir=tmp_path/'book/book_1', source_novel_dir=tmp_path/'book',
                          clip_seconds_max=15)
    with patch.dict(os.environ, {**ENDPOINTS['phanthy-ds41'],
            'PHANROUTER_IMAGE_API_KEY': 'image-test', 'PHANROUTER_H3CLOUD_API_KEY': 'h3-test'}, clear=True):
        env = resolve_agent_environment(ctx, {**read_json(ROOT/'configs/agent_sandbox.json'), 'video_channel': channel})
    assert env['NOVEL_VIDEO_MODEL'] == model
    assert env['NOVEL_LOCAL_H3_URL'] == ''
    assert env['PHANROUTER_VIDEO_KEY_VAR'] == key_var
    assert env['NOVEL_CLIP_SECONDS_MAX'] == '15'
    assert read_json(ctx.episode_dir/'video-channel.json')['name'] == channel
    resumed = resolve_agent_environment(ctx, read_json(ROOT/'configs/agent_sandbox.json'))
    assert resumed['NOVEL_VIDEO_MODEL'] == model and resumed['NOVEL_LOCAL_H3_URL'] == ''
    other = 'sd20-fast' if channel == 'h3cloud' else 'h3cloud'
    with pytest.raises(ValueError, match='channel'):
        resolve_agent_environment(ctx, {**read_json(ROOT/'configs/agent_sandbox.json'), 'video_channel': other})


@pytest.mark.parametrize('model', ['sd2.0-fast', 'sd2.0-mini'])
def test_fast_mini_settings_and_duration_guard(model):
    with patch.dict(os.environ, {'NOVEL_VIDEO_MODEL': model, 'PHANROUTER_VIDEO_KEY_VAR': 'PHANROUTER_IMAGE_API_KEY',
                                'PHANROUTER_IMAGE_API_KEY': 'test-only'}, clear=True):
        assert Settings.from_env(provider='phanrouter', admission_mode='preview').video_model == model
    with pytest.raises(ValueError, match='15 seconds'):
        video_payload(SimpleNamespace(video_model=model), 'Keep all authored words.', None, 20)


@pytest.mark.parametrize('channel,model,key_var', CHANNELS)
def test_cloud_request_resumes_the_existing_task_without_a_second_post(tmp_path, monkeypatch, channel, model, key_var):
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR', str(tmp_path/'quota'))
    settings = Settings(video_model=model, phanrouter_api_key='test-only', inline_reference_images=True)
    calls = []
    def handle(request):
        if request.method == 'POST':
            payload = json.loads(request.content)
            assert payload['model'] == model and payload['generate_audio']
            calls.append(payload)
            return httpx.Response(200, json={'task_id': 'existing-cloud-job'})
        if request.url.host == 'cdn.invalid':
            return httpx.Response(200, content=b'actual mocked cloud video')
        return httpx.Response(200, json={'data': {'status': 'succeeded', 'url': 'https://cdn.invalid/video.mp4'}})
    provider = PhanRouterMediaProvider(settings)
    provider.client.close()
    provider.client = httpx.Client(transport=httpx.MockTransport(handle))
    ref = tmp_path/'reference.jpeg'
    Image.new('RGB', (32, 48)).save(ref)
    out = tmp_path/'clip.mp4'
    try:
        for _ in range(2):
            provider.create_video('Approved request.', ImageResult(ref), out, 6)
        assert len(calls) == 1
        assert read_json(out.with_suffix('.mp4.task.json'))['task_id'] == 'existing-cloud-job'
        assert out.read_bytes() == b'actual mocked cloud video'
    finally:
        provider.client.close()


@pytest.mark.parametrize('model', ['sd2.0-fast', 'sd2.0-mini'])
def test_provided_first_frame_is_one_image_with_native_audio(tmp_path, monkeypatch, model):
    provider = PhanRouterMediaProvider(Settings(video_model=model))
    frame = tmp_path/'opening.jpeg'
    Image.new('RGB', (32, 48)).save(frame)
    seen = []
    monkeypatch.setattr(provider, '_restore_image_url', lambda image: 'https://assets.invalid/opening.jpeg')
    monkeypatch.setattr('novel_manga.providers.phanrouter_video.generate_video',
                        lambda settings, client, headers, payload, *a, **kw: seen.append(payload))
    try:
        provider.create_video('Approved SD request.', None, tmp_path/'clip.mp4', 6,
                              additional_images=(frame,), first_frame=frame)
        assert seen[0]['content'][1]['role'] == 'first_frame'
        assert seen[0]['generate_audio'] and len(seen[0]['content']) == 2
        with pytest.raises(ValueError, match='native audio'):
            provider.create_video('Request.', None, tmp_path/'clip.mp4', 6,
                                  additional_images=(frame,), reference_audios=(frame,), first_frame=frame)
    finally:
        provider.client.close()


@pytest.mark.parametrize('channel,model,key_var', CHANNELS)
def test_long_existing_plans_are_not_silently_clamped_by_a_new_channel(tmp_path, channel, model, key_var):
    from novel_manga.application.video_channels import channel_environment
    atomic_write_json(tmp_path/'configs/pipeline.json', read_json(ROOT/'configs/pipeline.json'))
    episode = tmp_path/'book/book_1'
    atomic_write_json(episode/'clip_plan.json', {'clips': [{'clip_id':'clip_01','kind':'video','request_seconds':22}]})
    before = (episode/'clip_plan.json').read_bytes()
    with pytest.raises(ValueError, match='15'):
        channel_environment(tmp_path, channel, episode_dir=episode)
    assert (episode/'clip_plan.json').read_bytes() == before
    assert not (episode/'video-channel.json').exists()


def test_cloud_h3_production_checks_the_final_h3_request_before_submission(tmp_path):
    from novel_manga.media import generation
    ctx = SimpleNamespace(settings=Settings(video_model='MiniMax-H3'),
        provider=SimpleNamespace(create_video=lambda *a,**kw: pytest.fail('invalid H3 reached provider')))
    request = {'prompt':'【阶段1】镜头向前移动，人物站在庭院。', 'reference_audios':[]}
    clip = {'clip_id':'clip_01','request_seconds':6,'shot_indexes':[1],'references':[],'stage_count':1}
    with pytest.raises(ValueError, match='H3 final request invalid'):
        generation.submit(ctx, clip, request, tmp_path/'clip.mp4', ())


def test_a_saved_take_cannot_be_relabelled_as_a_new_cloud_model(tmp_path):
    from novel_manga.application.video_channels import channel_environment
    atomic_write_json(tmp_path/'configs/pipeline.json', read_json(ROOT/'configs/pipeline.json'))
    episode = tmp_path/'book/book_1'
    take = episode/'work/clips/clip_01/attempt_01/clip.mp4'
    take.parent.mkdir(parents=True);take.write_bytes(b'original take')
    atomic_write_json(take.with_suffix('.mp4.task.json'), {'model':'minimax-h3-ref2va-turbo','task_id':'original'})
    with pytest.raises(ValueError, match='Existing media'):
        channel_environment(tmp_path, 'sd20-mini', episode_dir=episode)
    assert take.read_bytes() == b'original take'


@pytest.mark.parametrize('model', ['sd2.0-fast', 'sd2.0-mini'])
def test_renderer_records_the_supplied_first_frame_as_its_actual_input(tmp_path, model):
    from novel_manga.media import generation
    from novel_manga.media.context import RenderContext
    ctx = RenderContext();ctx.novel_dir=tmp_path;ctx.settings=Settings(video_model=model)
    ctx.profile={'sd_input_mode':'first-frame'};ctx.feedback={}
    frame=tmp_path/'opening.jpeg';Image.new('RGB',(32,48)).save(frame)
    clip={'clip_id':'clip_01','request_seconds':6,'prompt':'【人物】@图片2是甲\n【阶段1】@图片2静静坐着。',
          'references':[],'first_frame':{'path':'opening.jpeg'}}
    request,refs,_,_=generation.build_request(ctx,clip,1)
    assert refs==(frame,) and request['first_frame']==str(frame)
    assert '@图片2' not in request['prompt'] and '【首帧】' in request['prompt']


def test_cloud_cli_dry_run_does_not_freeze_or_write_an_episode_channel(tmp_path, monkeypatch, capsys):
    from novel_manga.application.rendering import cli
    atomic_write_json(tmp_path/'configs/pipeline.json', read_json(ROOT/'configs/pipeline.json'))
    book=tmp_path/'book';episode=book/'book_1'
    atomic_write_json(book/'story_bible.json', {'novel_id':'book','novel_title':'book','characters':[],
                                             'locations':[],'palette':'blue','visual_style':'3d','genre':'generic',
                                             'style_fingerprint':'fixture'})
    atomic_write_json(book/'profile.json', {'style':'3d','frame':'16:9','tier':'fast'})
    atomic_write_json(episode/'chapter_script.json', {'shots':[]})
    atomic_write_json(episode/'clip_plan.json', {'clips':[]})
    monkeypatch.setattr(cli,'ROOT',tmp_path)
    monkeypatch.setattr('sys.argv',['render_clips_thin.py','--novel-dir',str(book),'--episode','book_1',
                                   '--video-channel','sd20-fast','--dry-run'])
    with patch.dict(os.environ, {'PHANROUTER_IMAGE_API_KEY':'test-only'}, clear=True):
        assert cli.main()==0
    assert not (episode/'video-channel.json').exists()


def test_cloud_h3_can_use_the_existing_h3_agent_executor():
    from novel_manga.application.configuration import validate_agent_h3
    pipeline=read_json(ROOT/'configs/pipeline.json')
    validate_agent_h3([pipeline['resources']['video_keys']['h3cloud']])


@pytest.mark.parametrize('channel,model,key_var', CHANNELS)
def test_conductor_clears_inherited_local_h3_for_cloud_work(channel,model,key_var):
    from novel_manga.application.production.conductor_workers import key_env
    resource=read_json(ROOT/'configs/pipeline.json')['resources']['video_keys'][channel]
    env=key_env(SimpleNamespace(), {'name':channel,**resource})
    assert env['NOVEL_LOCAL_H3_URL']=='' and env['NOVEL_VIDEO_MODEL']==model
    assert env['NOVEL_VIDEO_CHANNEL']==channel and env['PHANROUTER_VIDEO_KEY_VAR']==key_var
