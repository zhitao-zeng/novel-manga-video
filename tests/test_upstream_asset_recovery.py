"""Actual candidate cache/records; only model and image execution are simulated."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from pathlib import Path
import copy
import pytest
from PIL import Image

from novel_manga.util import atomic_write_json, read_json
from novel_manga.application.agents.episode import upstream_assets, draft_assets, input_repair
from support.episode_existing import prepared


@pytest.fixture
def ctx_pair(tmp_path, monkeypatch):
    shared = tmp_path/'shared-book'; shared.mkdir()
    contexts = []
    bible = {'novel_title':'测试','genre':'奇幻','visual_style':'3D','palette':'蓝',
             'characters':[{'name':'甲','gender':'女','age':'成年','appearance':'黑发',
                            'wardrobe':'白袍','voice_profile_id':'voice-old'},
                           {'name':'乙','appearance':'白发','wardrobe':'黑袍'}],
             'locations':['房间：石墙']}
    for number in range(2):
        folder = tmp_path/str(number); folder.mkdir()
        ctx, plan, _ = prepared(folder, monkeypatch)
        ctx.source_novel_dir = shared
        ctx.asset_snapshot_dir = ctx.run_dir/'assets-snapshot'
        for ref in plan['clips'][0]['references']:
            p = ctx.novel_dir/ref['path']; p.parent.mkdir(parents=True,exist_ok=True)
            if p.suffix=='.wav':p.write_bytes(b'original voice')
            else:Image.new('RGB',(32,32),'blue').save(p)
        voice = ctx.novel_dir/'voices/a.wav'; voice.parent.mkdir(exist_ok=True); voice.write_bytes(b'original voice')
        atomic_write_json(ctx.novel_dir/'story_bible.json', bible)
        (ctx.run_dir/'source.txt').write_text('甲是个男人。甲坐在宿舍。甲说：你来啦。')
        contexts.append(ctx)
    return contexts


@pytest.fixture
def approved_source(monkeypatch):
    def ask(parts, schema, **kwargs):
        assert kwargs['name']=='upstream_asset_diagnosis'
        return {'kind':'image','reason':'实际卡面与男性设定冲突','source_quote':'甲是个男人。',
                'replacement_asset_id':'','binding_shots':[]}
    monkeypatch.setattr(upstream_assets, 'ask_json', ask)
    monkeypatch.setattr(input_repair,'source_check',lambda *a,**kw:{'consistent':True,'problems':[]})


@pytest.fixture
def fake_image(monkeypatch):
    result = SimpleNamespace(calls=0)
    def build(ctx, directory, role, name, design):
        result.calls += 1
        path = directory/('characters/character_001/turnaround.jpeg' if role=='character'
                          else 'locations/location_001/establishing.jpeg')
        path.parent.mkdir(parents=True,exist_ok=True); Image.new('RGB',(32,32),'red').save(path)
        return path
    monkeypatch.setattr(draft_assets,'build',build)
    monkeypatch.setattr(draft_assets,'check_image',lambda *a:{'consistent':True,'problems':[]})
    return result


def row(**changes):
    return {'clip_id':'clip_01','action':'repair_assets','asset_id':'a','source_quote':'甲是个男人。',
            'reason':'原文明示男性，卡面错误','fields':{'gender':'男'},**changes}


def test_two_run_trees_share_one_source_checked_card(ctx_pair,approved_source,fake_image):
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda ctx:upstream_assets.prepare(ctx,row()),ctx_pair))
    assert results[0]['asset']['asset_id']==results[1]['asset']['asset_id']
    assert fake_image.calls==1
    assert all(r['source']['consistent'] and r['image_review']['consistent'] for r in results)


@pytest.mark.parametrize('kind',['binding','uncertain','false_positive'])
def test_binding_fix_does_not_draw(ctx_pair,approved_source,fake_image,monkeypatch,kind):
    monkeypatch.setattr(upstream_assets,'ask_json',lambda *a,**k:{'kind':kind,'reason':'需核对归属',
        'source_quote':'甲是个男人。','replacement_asset_id':'b','binding_shots':[]})
    result=upstream_assets.prepare(ctx_pair[0],row())
    assert result['kind']==kind and result['asset'] is None and fake_image.calls==0
    assert result['ok']==(kind!='uncertain')


def test_uncertain_identity_draws_nothing(ctx_pair,approved_source,fake_image,monkeypatch):
    monkeypatch.setattr(upstream_assets,'ask_json',lambda *a,**k:{'kind':'uncertain','reason':'泛称不能认定同人',
        'source_quote':'甲是个男人。','replacement_asset_id':'','binding_shots':[]})
    assert upstream_assets.prepare(ctx_pair[0],row())['ok'] is False
    assert fake_image.calls==0


def test_unspecified_age_is_not_source_contradiction(ctx_pair,approved_source,fake_image,monkeypatch):
    seen=[]
    def ask(parts,*a,**kw):
        seen.extend(p.get('text','')for p in parts)
        return {'kind':'false_positive','reason':'原文没有约束衣料','source_quote':'甲坐在宿舍',
                'replacement_asset_id':'','binding_shots':[]}
    monkeypatch.setattr(upstream_assets,'ask_json',ask)
    result=upstream_assets.prepare(ctx_pair[0],row(source_quote='甲坐在宿舍',fields={'age':'成年'}))
    assert result['kind']=='false_positive' and fake_image.calls==0
    assert '原文未明确' in ''.join(seen)


def test_rejected_card_keeps_original_and_voice(ctx_pair,approved_source,fake_image,monkeypatch):
    ctx=ctx_pair[0];before=(ctx.novel_dir/'cards/a.jpeg').read_bytes();voice=(ctx.novel_dir/'voices/a.wav').read_bytes()
    def reject(*a):raise input_repair.UnsupportedInput('卡面仍不一致')
    monkeypatch.setattr(draft_assets,'check_image',reject)
    with pytest.raises(input_repair.UnsupportedInput):upstream_assets.prepare(ctx,row())
    with pytest.raises(input_repair.UnsupportedInput):upstream_assets.prepare(ctx,row())
    assert fake_image.calls==1
    assert (ctx.novel_dir/'cards/a.jpeg').read_bytes()==before
    assert (ctx.novel_dir/'voices/a.wav').read_bytes()==voice


def test_different_base_assets_do_not_share_candidate(ctx_pair,approved_source,fake_image):
    shared=copy.copy(ctx_pair[0]);shared.asset_snapshot_dir=shared.source_novel_dir
    design={'gender':'男','age':'成年','appearance':'黑发','wardrobe':'白袍'}
    a=draft_assets._asset(shared,'character','甲',design,base_asset='a')
    b=draft_assets._asset(shared,'character','甲',design,base_asset='different-a')
    assert a['asset_id']!=b['asset_id'] and fake_image.calls==2


def test_snapshot_adoption_keeps_other_actor_fields(ctx_pair,approved_source,fake_image):
    ctx=ctx_pair[0];before=read_json(ctx.novel_dir/'story_bible.json');old_plan=(ctx.episode_dir/'clip_plan.json').read_bytes()
    result=upstream_assets.prepare(ctx,row());adopted=upstream_assets.apply_to_snapshot(ctx,result)
    assert adopted['ok'] and adopted['input_decision']['action']=='input'
    assert read_json(ctx.novel_dir/'story_bible.json')==before
    assert (ctx.episode_dir/'clip_plan.json').read_bytes()==old_plan
    phases=read_json(ctx.novel_dir/'series_assets/phases.json')['characters']['甲']
    assert phases[-1]['gender']=='男'
    assert (ctx.novel_dir/'series_assets/characters'/result['asset']['asset_id']/'turnaround.jpeg').is_file()
    assert input_repair.remaining_input_attempts(ctx,['clip_01'])['clip_01']==2
