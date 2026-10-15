from types import SimpleNamespace
import pytest
from novel_manga.application.assets import candidates
from novel_manga.util import atomic_write_json


@pytest.mark.parametrize('role,design',[('location','flooded room'),('character',{'appearance':'adult','wardrobe':'gray clothes'})])
def test_actual_shared_builder_constructs_typed_design_before_supplier(tmp_path,monkeypatch,role,design):
    from novel_manga.config import Settings
    from novel_manga.application import profiles
    from novel_manga.media import asset_style,asset_builder,adapters
    monkeypatch.setattr(Settings,'from_env',lambda **k:Settings())
    monkeypatch.setattr(profiles,'styled_bible',lambda bible,*a:bible)
    monkeypatch.setattr(asset_style.AssetStyle,'for_genre',lambda *a,**k:object())
    monkeypatch.setattr(adapters,'FramedPhanRouter',lambda *a:object())
    seen=[]
    class Factory:
        def __init__(self,*a,**kw):pass
        def build_selected(self,directory,bible,*a,**kw):seen.append(bible)
    monkeypatch.setattr(asset_builder,'FramedAssetFactory',Factory)
    atomic_write_json(tmp_path/'story_bible.json',{'novel_title':'case','genre':'generic','visual_style':'live','palette':'gray','style_fingerprint':'case'})
    ctx=SimpleNamespace(novel_dir=tmp_path,run_dir=tmp_path)
    path=candidates.build(ctx,tmp_path,role,'A',design)
    assert path.name in {'turnaround.jpeg','establishing.jpeg'}
    assert seen and (seen[0].characters[0].name=='A' if role=='character' else seen[0].locations==['A：flooded room'])


def test_actual_image_reviewer_reaches_existing_judge_without_unbound_name(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(candidates,'image_part',lambda *a:{'type':'image'})
    monkeypatch.setattr(candidates,'ask_json',lambda *a,**kw:calls.append(kw)or{'consistent':True,'problems':[]})
    assert candidates.check_image(tmp_path/'candidate.jpg','location','A','water')['consistent']
    assert calls[0]['name']=='draft_asset_image'
