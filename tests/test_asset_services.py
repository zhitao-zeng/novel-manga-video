import json
import tempfile
from pathlib import Path
from novel_manga.models.bible import StoryBible, Character
from novel_manga.config import Settings
from novel_manga.providers.base import ImageResult
from novel_manga.media.asset_builder import FramedAssetFactory
from novel_manga.media.asset_style import AssetStyle
from PIL import Image


def frozen_assets():
    results=[]
    for style,frame,expressions in [('二维国漫','竖屏9:16',False),('3D国漫','横屏16:9',False),('二维国漫','横屏16:9',True)]:
     with tempfile.TemporaryDirectory(prefix='nmv-card-freeze-') as tmp:
      base=Path(tmp);calls=[]
      class Provider:
       def create_image(self,prompt,output,**kwargs):
        # The old facade injected frame after this boundary; payload parity is tested separately.
        calls.append({'prompt':prompt,'output':str(output.relative_to(base)),**{k:str(v.relative_to(base)) if isinstance(v,Path) else v for k,v in kwargs.items() if k != "aspect_ratio"}})
        output.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(64,64),'blue').save(output)
        return ImageResult(path=output)
      settings=Settings(reuse_existing_assets=True)
      factory=FramedAssetFactory(settings,Provider(),style=AssetStyle(frame_text=frame))
      bible=StoryBible(novel_title='测试',genre='generic',visual_style=style,palette='蓝',style_fingerprint='fixed',characters=[Character(name='甲',appearance='黑发',wardrobe='蓝衣')],locations=['厅堂'])
      manifest=factory.build_selected(base/'series_assets',bible,{'character_001'},{'location_001'},expressions=expressions)
      count=len(calls)
      factory.build_selected(base/'series_assets',bible,{'character_001'},{'location_001'},expressions=expressions)
      files={str(p.relative_to(base)):json.loads(p.read_text()) for p in sorted(base.rglob('*.json'))}
      results.append({'calls':calls,'cache_reused':len(calls)==count,'manifest':manifest.model_dump(mode='json'),'files':files})
    return results


def test_asset_requests_records_and_reuse_match_the_previous_builder():
    expected = json.loads((Path(__file__).parent / 'fixtures/asset_services_before.json').read_text())
    assert frozen_assets() == expected


def test_asset_configuration_returns_to_defaults_for_another_book():
    custom = AssetStyle.for_genre({'card_style_suffix_3d': 'custom 3D', 'location_policy': 'sparse'}, frame_text='横屏16:9')
    plain = AssetStyle.for_genre({})
    assert plain == AssetStyle()
    assert custom.card_style_suffix_3d == 'custom 3D' and custom.location_empty_suffix != plain.location_empty_suffix
    assert custom.frame_text == '横屏16:9' and plain.frame_text == '竖屏9:16'


def test_a_style_package_can_word_itself_differently_per_image_model():
    """gpt-image knows 选角定妆照 and draws one; Qwen draws a poster from the same words and
    needs the frame spelled out. One package with a section per model, not two that drift."""
    from novel_manga.config import Settings
    from novel_manga.media.asset_style import image_backend

    package = {
        'render_family': '2d',
        'render_direction': '墨线平涂',
        'qwen': {'card_brief': '背景是一整块高饱和的纯色。', 'prompt_fingerprint': False},
    }
    default = AssetStyle.for_genre({}, style=package)
    qwen = AssetStyle.for_genre({}, style=package, backend='qwen')

    assert default.card_brief == '' and default.prompt_fingerprint is True
    assert qwen.card_brief == '背景是一整块高饱和的纯色。' and qwen.prompt_fingerprint is False
    # Everything the two models share is still stated once.
    assert default.render_direction == qwen.render_direction == '墨线平涂'
    # A package with no section for the backend is left exactly as it is.
    assert AssetStyle.for_genre({}, style={'render_direction': '墨线平涂'}, backend='qwen').card_brief == ''

    assert image_backend(Settings()) == ''
    assert image_backend(Settings(local_image_base_url='http://qwen.invalid')) == 'qwen'
