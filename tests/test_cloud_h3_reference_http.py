"""HTTP transport for existing cloud-H3 references; probes are simulated, no external API."""
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote,urlsplit
import base64,hashlib,json
import httpx
import pytest
from PIL import Image
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter_references import ReferenceMaterials
from novel_manga.media import publish

@pytest.fixture
def case(tmp_path,monkeypatch):
    root=tmp_path/'published';monkeypatch.setenv('NOVEL_ASSET_PUBLISH_DIR',str(root))
    settings=SimpleNamespace(video_model='MiniMax-H3',phanrouter_asset_public_base='https://public.example/published',
        inline_reference_images=False,reference_images_via_assets=False,request_timeout=20,output_root=tmp_path/'outputs')
    path=tmp_path/'outputs'/'知遥·云鲸 驿站'/'series_assets'/'characters'/'character_001'/'turnaround.jpeg'
    path.parent.mkdir(parents=True);Image.new('RGB',(48,64),'green').save(path)
    return settings,path,root

class Probe:
    def __init__(self,root,status=200):self.root=root;self.calls=[];self.status=status
    def head(self,url,**kwargs):
        self.calls.append((url,kwargs))
        relative=unquote(urlsplit(url).path.split('/published/',1)[1])
        assert(self.root/relative).is_file(), 'publish must complete before probe'
        assert kwargs['headers']=={'ngrok-skip-browser-warning':'1'}and kwargs['follow_redirects']is True
        return SimpleNamespace(status_code=self.status)
    def post(self,*a,**kw):pytest.fail('reference restoration submitted an API task')

@pytest.mark.parametrize('crop',[False,True])
def test_cloud_h3_publishes_original_or_crop_without_image_task(case,tmp_path,crop):
    settings,path,root=case
    if crop:
        path=tmp_path/'outputs'/'云鲸·当前集'/'work'/'reference-views'/'clip_01'/'面部 crop.jpeg'
        path.parent.mkdir(parents=True);Image.new('RGB',(24,24),'blue').save(path)
    original=path.read_bytes();assert not path.with_suffix('.jpeg.task.json').exists()
    client=Probe(root);refs=ReferenceMaterials(settings,client,{'Authorization':'not-for-probe'},{})
    out=refs._restore_image_url(ImageResult(path=path,public_url='https://old.example/expired'))
    assert out.startswith(settings.phanrouter_asset_public_base+'/')and len(client.calls)==1
    assert '%E'in out and' 'not in out
    published=root/unquote(urlsplit(out).path.split('/published/',1)[1])
    assert published.read_bytes()==original and path.read_bytes()==original
    assert not path.with_suffix('.jpeg.task.json').exists()and not path.with_suffix('.jpeg.asset.json').exists()

def test_cloud_h3_prefers_http_even_if_asset_flag_is_inherited(case,monkeypatch):
    settings,path,root=case;settings.reference_images_via_assets=True
    refs=ReferenceMaterials(settings,Probe(root),{},{});monkeypatch.setattr(refs,'_asset_reference',lambda *a:pytest.fail('cloud H3 used asset://'))
    assert refs._restore_image_url(ImageResult(path=path)).startswith('https://public.example/')

@pytest.mark.parametrize('model',['sd2.0-fast','sd2.0-mini'])
def test_fast_mini_asset_route_is_unchanged(case,monkeypatch,model):
    settings,path,root=case;settings.video_model=model;settings.reference_images_via_assets=True
    refs=ReferenceMaterials(settings,Probe(root),{},{});seen=[]
    monkeypatch.setattr(refs,'_published_image_url',lambda *a:pytest.fail('new H3 publisher affected Fast/Mini'))
    monkeypatch.setattr(refs,'_asset_reference',lambda image:seen.append(image.path)or'asset://existing')
    assert refs._restore_image_url(ImageResult(path=path))=='asset://existing'and seen==[path]

@pytest.mark.parametrize('remote',[False,True])
def test_inline_route_including_existing_public_url_priority_is_unchanged(case,monkeypatch,remote):
    settings,path,root=case;settings.inline_reference_images=True
    refs=ReferenceMaterials(settings,Probe(root),{},{});monkeypatch.setattr(refs,'_published_image_url',lambda *a:pytest.fail('inline reference was published'))
    url='https://old.example/reference'if remote else None
    result=refs._restore_image_url(ImageResult(path=path,public_url=url))
    if remote:assert result==url
    else:
        assert result.startswith('data:image/jpeg;base64,')and base64.b64decode(result.split(',',1)[1])
    assert not root.exists()

def test_other_model_without_task_keeps_existing_failure(case):
    settings,path,root=case;settings.video_model='sd2.0-fast';client=Probe(root)
    with pytest.raises(ValueError,match='task metadata is missing'):ReferenceMaterials(settings,client,{},{})._restore_image_url(ImageResult(path=path))
    assert client.calls==[]and not root.exists()

def test_cloud_h3_without_public_base_keeps_provider_task_restore(case,monkeypatch):
    from novel_manga.providers import phanrouter_references
    settings,path,root=case;settings.phanrouter_asset_public_base=None
    path.with_suffix('.jpeg.task.json').write_text(json.dumps({'task_id':'existing-image-job'}))
    calls=[];monkeypatch.setattr(phanrouter_references,'poll_image_url',lambda *a:calls.append(a[-1])or'https://hosted.example/image')
    assert ReferenceMaterials(settings,Probe(root),{},{})._restore_image_url(ImageResult(path=path))=='https://hosted.example/image'
    assert calls==['existing-image-job']and not root.exists()

def test_probe_http_error_stops_without_url_credentials_or_response_leak(case):
    settings,path,root=case;settings.phanrouter_asset_public_base='https://private.example/secret-path/published';client=Probe(root,404)
    with pytest.raises(RuntimeError)as error:ReferenceMaterials(settings,client,{},{})._restore_image_url(ImageResult(path=path))
    message=str(error.value);assert'HTTP 404'in message and'PHANROUTER_ASSET_PUBLIC_BASE'in message
    assert'https://'not in message and'private.example'not in message and'secret-path'not in message

def test_network_probe_error_is_sanitized_and_does_not_fallback_or_submit(case):
    settings,path,root=case
    class Offline:
        def head(self,url,**kwargs):raise httpx.ConnectError('secret download URL: '+url,request=httpx.Request('HEAD',url))
        def post(self,*a,**kw):pytest.fail('network probe caused video submission')
    with pytest.raises(RuntimeError)as error:ReferenceMaterials(settings,Offline(),{},{})._restore_image_url(ImageResult(path=path))
    assert'ConnectError'in str(error.value)and'https://'not in str(error.value)
    assert error.value.__suppress_context__

def test_publish_uses_existing_digest_once_and_does_not_rewrite_equal_copy(case,monkeypatch):
    settings,path,root=case;count=[];sha=hashlib.sha256
    monkeypatch.setattr(publish.hashlib,'sha256',lambda data:count.append(1)or sha(data))
    client=Probe(root);refs=ReferenceMaterials(settings,client,{},{});first=refs._restore_image_url(ImageResult(path=path))
    target=root/unquote(urlsplit(first).path.split('/published/',1)[1]);before=target.stat().st_mtime_ns
    assert count==[1]
    assert refs._restore_image_url(ImageResult(path=path))==first and target.stat().st_mtime_ns==before
    assert count==[1,1]and len(client.calls)==2


def test_cloud_and_fast_keep_one_identical_public_transport_copy(tmp_path,monkeypatch):
    import random
    root=tmp_path/'published';monkeypatch.setenv('NOVEL_ASSET_PUBLISH_DIR',str(root))
    path=tmp_path/'book/series_assets/characters/character_001/turnaround.jpeg'
    path.parent.mkdir(parents=True)
    Image.frombytes('RGB',(1024,1024),random.Random(7).randbytes(1024*1024*3)).save(path,format='PNG')
    original=path.read_bytes();assert len(original)>2*1024*1024
    settings=SimpleNamespace(video_model='MiniMax-H3',phanrouter_asset_public_base='https://public.example/published',
        inline_reference_images=False,reference_images_via_assets=True,phanrouter_asset_group_id='group',
        phanrouter_base_url='https://api.invalid',request_timeout=20,poll_timeout=20,output_root=tmp_path)
    class Client(Probe):
        def head(self,url,**kwargs):
            result=super().head(url,**kwargs)
            target=self.root/unquote(urlsplit(url).path.split('/published/',1)[1])
            with Image.open(target) as image:assert image.format=='JPEG'
            return result
        def post(self,url,**kwargs):
            data={'Id':'one-material'} if url.endswith('CreateAsset') else {'Status':'Active'}
            return SimpleNamespace(status_code=200,text='',raise_for_status=lambda:None,json=lambda:{'data':data})
    client=Client(root);refs=ReferenceMaterials(settings,client,{},{})
    url=refs._restore_image_url(ImageResult(path));target=root/unquote(urlsplit(url).path.split('/published/',1)[1])
    transport=target.read_bytes()
    settings.video_model='sd2.0-fast'
    assert refs._restore_image_url(ImageResult(path))=='asset://one-material'
    assert target.read_bytes()==transport and path.read_bytes()==original
