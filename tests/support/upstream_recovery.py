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


