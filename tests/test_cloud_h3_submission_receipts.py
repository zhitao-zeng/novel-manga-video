"""Native H3 envelopes and retained receipts across changed requests."""
import json

import httpx
import pytest
from PIL import Image

from novel_manga.config import Settings
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter import PhanRouterMediaProvider
from novel_manga.providers.phanrouter_tasks import SubmissionUncertain
from novel_manga.util import read_json


def card(root):
    p=root/'shiri/series_assets/characters/character_001/turnaround.jpeg'
    p.parent.mkdir(parents=True,exist_ok=True)
    Image.new('RGB',(48,64),'green').save(p)
    return p


@pytest.mark.parametrize('unknown',[False,True])
@pytest.mark.parametrize('change',['none','prompt','image'])
def test_inline_reference_request_resumes_or_holds_original_task_without_another_post(tmp_path,monkeypatch,unknown,change):
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path/'quota'))
    path=card(tmp_path);output=tmp_path/'clip.mp4';posts=[];signatures=[]
    def handle(request):
        if request.method=='POST':
            posts.append(1)
            if unknown:return httpx.Response(500,text='create response lost')
            return httpx.Response(200,json={'task_id':'existing-task'})
        if request.url.host=='video.test':
            return httpx.Response(200,content=b'completed cloud video')
        if len(signatures)==1:raise httpx.ReadTimeout('poll interrupted',request=request)
        return httpx.Response(200,json={'data':{'status':'succeeded','url':'https://video.test/clip.mp4'}})
    def provider():
        p=PhanRouterMediaProvider(Settings(video_model='MiniMax-H3',phanrouter_api_key='test'))
        p.client.close();p.client=httpx.Client(transport=httpx.MockTransport(handle))
        signatures.append(len(signatures)+1)
        return p
    with pytest.raises(SubmissionUncertain if unknown else httpx.ReadTimeout):
        provider().create_video('Same approved input.',ImageResult(path),output,8)
    if change=='image':Image.new('RGB',(48,64),'red').save(path)
    prompt='Changed approved input.' if change=='prompt' else 'Same approved input.'
    if unknown or change!='none':
        with pytest.raises(SubmissionUncertain):
            provider().create_video(prompt,ImageResult(path),output,8)
    else:
        provider().create_video('Same approved input.',ImageResult(path),output,8)
        assert output.read_bytes()==b'completed cloud video'
    assert len(posts)==1
    receipt=read_json(output.with_suffix('.mp4.task.json'))
    assert receipt.get('submit_uncertain_at') if unknown else receipt['task_id']=='existing-task'


def test_h3_uses_native_creation_contract_and_keeps_common_task_lookup(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path/'quota'))
    paths=[]
    def handle(request):
        paths.append((request.method,request.url.path))
        if request.method=='POST':
            assert request.url.path=='/v2/video_generation'
            body=json.loads(request.content)
            assert body['aigc_watermark'] is False and body['duration']==7
            assert not {'output_format','watermark','generate_audio'}&body.keys()
            return httpx.Response(200,json={'code':0,'data':{'task_id':'native-h3'}})
        if request.url.host=='cdn.test':return httpx.Response(200,content=b'native generated video')
        return httpx.Response(200,json={'data':{'status':'succeeded','url':'https://cdn.test/video.mp4'}})
    p=PhanRouterMediaProvider(Settings(video_model='MiniMax-H3',phanrouter_api_key='test-only',phanrouter_base_url='https://router.test'))
    p.client.close();p.client=httpx.Client(transport=httpx.MockTransport(handle))
    out=tmp_path/'native.mp4';p.create_video('Approved text-only input.',None,out,7)
    assert out.read_bytes()==b'native generated video'
    assert ('GET','/api/v3/contents/generations/tasks/native-h3')in paths


def test_accepted_response_without_task_id_is_held_and_not_reposted(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path/'quota'));calls=[]
    def handle(request):
        calls.append(1);return httpx.Response(200,json={'code':0,'data':{}})
    p=PhanRouterMediaProvider(Settings(video_model='MiniMax-H3',phanrouter_api_key='test-only'))
    p.client.close();p.client=httpx.Client(transport=httpx.MockTransport(handle));out=tmp_path/'missing.mp4'
    for _ in range(2):
        with pytest.raises(SubmissionUncertain):p.create_video('Approved text-only input.',None,out,7)
    assert len(calls)==1 and read_json(out.with_suffix('.mp4.task.json'))['submit_uncertain_at']


@pytest.mark.parametrize('unknown',[False,True])
def test_legacy_http_receipt_survives_inline_transport_migration(tmp_path,monkeypatch,unknown):
    from novel_manga.util import atomic_write_json
    monkeypatch.setenv('NOVEL_SHARED_QUOTA_DIR',str(tmp_path/'quota'))
    path=card(tmp_path);output=tmp_path/'clip.mp4';receipt=output.with_suffix('.mp4.task.json')
    original={'request_sha256':'legacy-http-payload','model':'MiniMax-H3'}
    original.update({'submit_uncertain_at':1e12} if unknown else {'task_id':'original-accepted-task'})
    atomic_write_json(receipt,original)
    def reject_post(request):pytest.fail('Transport migration submitted a second task')
    p=PhanRouterMediaProvider(Settings(video_model='MiniMax-H3',phanrouter_api_key='test-only'))
    p.client.close();p.client=httpx.Client(transport=httpx.MockTransport(reject_post))
    with pytest.raises(SubmissionUncertain):
        p.create_video('Same approved input.',ImageResult(path),output,7)
    assert read_json(receipt)==original
