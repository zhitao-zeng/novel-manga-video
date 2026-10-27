"""Cloud H3 local references do not depend on public URL retrieval."""
import base64
from dataclasses import replace
import io
from pathlib import Path

import httpx
import pytest
from PIL import Image

from novel_manga.config import Settings
from novel_manga.providers.base import ImageResult
from novel_manga.providers.phanrouter_references import ReferenceMaterials


@pytest.mark.parametrize('asset_mode',[False,True])
@pytest.mark.parametrize('wide',[False,True])
def test_h3_uploads_complete_local_card_despite_public_asset_flags(tmp_path,asset_mode,wide):
    path=tmp_path/'参考图.jpeg'
    Image.new('RGB',(1600,800) if wide else (800,1600),'green').save(path,format='PNG')
    original=path.read_bytes()
    def reject_network(request):
        pytest.fail('H3 reference preparation fetched an external URL or registered an asset')
    settings=Settings(video_model='MiniMax-H3',inline_reference_images=False,
        reference_images_via_assets=asset_mode,phanrouter_asset_group_id='group',
        phanrouter_asset_public_base='https://warning.test/published')
    with httpx.Client(transport=httpx.MockTransport(reject_network)) as client:
        url=ReferenceMaterials(settings,client,{},{})._restore_image_url(
            ImageResult(path,public_url='https://expired.test/image.jpeg'))
    assert url.startswith('data:image/jpeg;base64,')
    with Image.open(io.BytesIO(base64.b64decode(url.split(',',1)[1]))) as image:
        assert image.format=='JPEG' and image.size==((1280,640) if wide else (640,1280))
    assert path.read_bytes()==original
    assert list(tmp_path.iterdir())==[path]


@pytest.mark.parametrize('model',['sd2.0-fast','sd2.0-mini','sd2.5'])
def test_other_cloud_channels_keep_asset_or_public_url_priority(tmp_path,monkeypatch,model):
    settings=Settings(video_model=model,reference_images_via_assets=True,inline_reference_images=False)
    refs=ReferenceMaterials(settings,None,{},{})
    monkeypatch.setattr(refs,'_asset_reference',lambda image:'asset://existing')
    image=ImageResult(tmp_path/'card.jpeg',public_url='https://existing.test/card.jpeg')
    assert refs._restore_image_url(image)=='asset://existing'
    refs.settings=replace(settings,inline_reference_images=True)
    assert refs._restore_image_url(image)=='https://existing.test/card.jpeg'


def test_bad_h3_reference_fails_before_any_submission_without_printing_source(tmp_path):
    path=tmp_path/'broken.jpeg';path.write_text('not an image')
    refs=ReferenceMaterials(Settings(video_model='MiniMax-H3'),None,{},{})
    with pytest.raises(RuntimeError,match='H3 reference encoding failed') as error:
        refs._restore_image_url(ImageResult(path,public_url='https://private.test/signed?secret=hidden'))
    assert 'private.test'not in str(error.value) and 'hidden'not in str(error.value)
