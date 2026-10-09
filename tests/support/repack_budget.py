"""Source limits survive recuts; pending submissions share the actual balance."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import copy
import pytest
from novel_manga.util import atomic_write_json, read_json
from novel_manga.application.repair import history
from novel_manga.application.agents.episode import scoped_repack
from support.split_episode import split_episode




@pytest.fixture
def repacked_episode(tmp_path):
    d=tmp_path/'book_1';d.mkdir()
    atomic_write_json(d/'clip_plan.json',{'clips':[{'clip_id':cid,'kind':'video'} for cid in ['clip_01','clip_06','clip_99']]})
    group={'old':['clip_01'],'new':['clip_01','clip_06'],
        'source_budget':{'roots':{'clip_01':{'limit':3,'used':1}},
            'children':{'clip_01':{'roots':['clip_01'],'baseline_count':1},
                        'clip_06':{'roots':['clip_01'],'baseline_count':0}}}}
    history.save(d,{'prior_generation_counts':{'clips':{'clip_01':1}},
                    'trials':[{'id':1,'method':'agent_scoped_repack','changes':{'groups':[group]}}]})
    return d


def output(d,cid):
    p=d/'work/clips'/cid/'attempt_01/clip.mp4';p.parent.mkdir(parents=True,exist_ok=True);return p


@pytest.fixture
def long_context(tmp_path,monkeypatch):
    d,base,plan=split_episode.__wrapped__(tmp_path,monkeypatch)
    from novel_manga.application.packing import context,service
    script={'shots':[]}
    for index,seconds in enumerate([5,5,6,6,6,7],1):
        shot=copy.deepcopy(base['shots'][0]);shot.update(index=index,origin_index=index,duration_seconds=seconds,
            visual_prompt='林凡站在窗边',motion_prompt='林凡看向大厅',turns=[])
        script['shots'].append(shot)
    legacy=copy.deepcopy(plan);legacy['limits'].update(max_clip_seconds=30,soft_cut_seconds=30,max_stages=5)
    ctx=context.context_for_plan(d,d.parent/'story_bible.json',legacy)
    shots=service.prepared_shots(copy.deepcopy(script),d)
    legacy['clips']=[service.clip_entry({'kind':'video','shots':shots[:5],'seconds':28,'location':'大厅'},'clip_01',ctx),
                     service.clip_entry({'kind':'video','shots':shots[5:],'seconds':7,'location':'大厅'},'clip_02',ctx)]
    atomic_write_json(d/'chapter_script.json',script);atomic_write_json(d/'clip_plan.json',legacy)
    run=tmp_path/'run';run.mkdir();(run/'source.txt').write_text('林凡走进大厅。')
    work=run/'sandbox';work.mkdir()
    from PIL import Image
    for c in legacy['clips']:
        for ref in c.get('references',[]):
            pic=d.parent/ref['path'];pic.parent.mkdir(parents=True,exist_ok=True);Image.new('RGB',(32,32),'blue').save(pic)
    legacy=read_json(d/'clip_plan.json')
    return SimpleNamespace(episode_dir=d,novel_dir=d.parent,source_novel_dir=d.parent,asset_snapshot_dir=d.parent,run_dir=run,work_dir=work,clip_seconds_max=15),legacy


def decision():return {'clip_id':'clip_01','action':'repack','clip_ids':['clip_01'],
                       'source_quote':'林凡走进大厅','reason':'原范围超过H3上限'}

