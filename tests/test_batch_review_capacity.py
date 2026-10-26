"""Idle text capacity serves input exceptions while host tools retain ownership."""
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from support.episode_conductor import setup, make, write, configure_pair
from novel_manga.application.production import batch_review, workspaces, agent_preparation
from novel_manga.util import read_json


def queue(setup, monkeypatch, count=16, parallel=12, pool=100):
    root, config, spawned = setup
    configure_pair(root, config, monkeypatch, count=count)
    config['novels'][0]['agent'].update(queue='batch_review', parallel=parallel,
        task_pool_size=pool, workspace_index=str(root/'index.json'))
    for endpoint in ('deepseek', 'flashnext'):
        path=root/f'configs/{endpoint}.json'
        write(path, {**read_json(path), 'production_mode':'batch_review'})
    c=make(config, root)
    index={}
    for n, row in c.agents.items():
        run=Path(row['run_dir']);source=Path(row['source_episode'])
        item={'episode':int(n), 'source_novel_dir':str(c.novel_dir),
            'source_episode_dir':str(source), 'run_dir':str(run),
            'novel_dir':str(run/'book'), 'episode_dir':str(run/'book'/source.name),
            'input_mode':'source'}
        write(Path(item['episode_dir'])/'clip_plan.json', {'clips':[{'clip_id':'clip_01','kind':'video','references':[]}]})
        workspaces.save(item, 'blocked', error='input needs repair')
        index[n]=item
    write(root/'index.json', index)
    return c,index,spawned


def active(c, n, phase='tool_running', status='running', action='render'):
    row=c.agents[str(n)];row.update(model_endpoint='deepseek')
    saved={'status':'running','phase':phase,'remaining_seconds':123,
        'session_id':f'existing-{n}', 'job':{'id':'job-1','action':action,'status':status}}
    path=Path(row['run_dir'])/'agent-run.json';write(path,saved)
    c.procs[f'agent_{n}']=SimpleNamespace(pid=800+n,poll=lambda:None)
    return path,path.read_bytes()


@pytest.mark.parametrize('phase,status,expected', [
    ('tool_running','running',12), ('tool_running','completed',10),
    ('agent_running','accepted',10)])
def test_tools_release_text_admission_only_after_agent_yields(setup, monkeypatch, phase,status,expected):
    c,_,spawned=queue(setup,monkeypatch)
    originals=[active(c,n,phase,status) for n in (1,2)]
    c.tick()
    assert len(spawned)==expected
    assert Counter(env['NOVEL_AGENT_MODEL'] for _,_,env in spawned)=={
        'agent-model':expected-4,'qwen-model':4}
    assert all(path.read_bytes()==before for path,before in originals)
    assert all('--retry-issues' not in cmd for _,cmd,_ in spawned)


def test_background_tools_keep_total_hosts_inside_task_pool(setup,monkeypatch):
    c,_,spawned=queue(setup,monkeypatch,count=8,pool=5)
    for n in range(1,6):active(c,n)
    c.tick()
    assert not spawned
    assert len(c.task_pool_episodes)==5


def test_input_exceptions_replace_idle_reviews_in_retained_pool(setup,monkeypatch):
    c,index,spawned=queue(setup,monkeypatch,count=6,parallel=2,pool=4)
    for n in range(1,5):
        d=Path(index[str(n)]['episode_dir'])/'work/clips/clip_01/attempt_01'
        d.mkdir(parents=True);(d/'clip.mp4').write_bytes(b'first video')
        workspaces.save(index[str(n)],'complete')
    c.task_pool_episodes=[1,2,3,4]
    c.tick()
    assert {name for name,_,_ in spawned}=={'agent_5','agent_6'}
    assert all(env['NOVEL_FIRST_PASS_ONLY']=='1' for _,_,env in spawned)
    assert {5,6}<=set(c.task_pool_episodes)
    assert c.agents['5']['batch_stage']=='input_repair'


def test_empty_video_stock_and_other_model_waiters_do_not_gate_text_repairs():
    peer=SimpleNamespace(tick=lambda **kwargs:True)
    producer=SimpleNamespace(dry=False,cfg={'review':{'backend':'episode_agent','ready_floor':12}},
        procs={'plan_1':SimpleNamespace(poll=lambda:None)},review_conductor=peer)
    batch_review.tick(producer,{1:{'ready_clips':0}},waiting=20)
    assert peer.agent_dispatch_enabled and producer.review_more


def test_batch_input_waits_for_existing_shared_images_without_taking_text_slot(setup,monkeypatch):
    c,_,spawned=queue(setup,monkeypatch,count=1)
    c.agent_settings['asset_manifest']=str(c.tmp/'assets.json')
    monkeypatch.setattr('novel_manga.application.production.asset_preparation.queued_asset_paths',
                        lambda *a,**k:['missing-card.jpeg'])
    c.tick()
    assert not spawned
    assert c.agents['1']['status']=='waiting_assets'


def test_qwen_affinity_cannot_strand_idle_ds_for_batch_review(setup,monkeypatch):
    c,_,spawned=queue(setup,monkeypatch)
    originals=[]
    for row in c.agents.values():
        row['model_endpoint']='flashnext';run=Path(row['run_dir'])
        write(run/'agent-run.json',{'status':'deferred','remaining_seconds':123,
            'session_id':str(row['episode']),'no_progress':1})
        write(run/'sandbox/episode-session.json',{'session_id':str(row['episode'])})
        write(run/'book'/Path(row['source_episode']).name/'repair_budget_grants.json',{'clip_01':{'limit':2}})
        for path in [run/'sandbox/episode-session.json',run/'book'/Path(row['source_episode']).name/'repair_budget_grants.json']:
            originals.append((path,path.read_bytes()))
    c.tick()
    assert Counter(env['NOVEL_AGENT_MODEL'] for _,_,env in spawned)=={'agent-model':8,'qwen-model':4}
    assert all(path.read_bytes()==before for path,before in originals)


def test_batch_fallback_remains_pinned_when_qwen_is_full(setup,monkeypatch):
    from novel_manga.application.production import agent_models
    c,_,_=queue(setup,monkeypatch,count=5)
    for n in range(1,5):c.agents[str(n)].update(pid=700+n,model_endpoint='flashnext',phase='agent_running')
    row=c.agents['5'];row.update(model_endpoint='flashnext',status='deferred')
    write(Path(row['run_dir'])/'agent-run.json',{'status':'deferred','remaining_seconds':123,
        'model_switch':{'to_model':'qwen-model'}})
    assert not agent_models.assign(c,row)
    assert row['model_endpoint']=='flashnext'


def test_completed_media_reserves_two_hosts_with_input_backlog(setup,monkeypatch):
    c,index,spawned=queue(setup,monkeypatch,count=18)
    c.agent_settings['review_reserve']=2
    for n in range(15,19):
        d=Path(index[str(n)]['episode_dir'])/'work/clips/clip_01/attempt_01'
        d.mkdir(parents=True);(d/'clip.mp4').write_bytes(b'first video')
        workspaces.save(index[str(n)],'complete')
    c.tick()
    assert len(spawned)==12
    assert sum(env['NOVEL_FIRST_PASS_ONLY']=='0' for _,_,env in spawned)==2
    assert sum(env['NOVEL_FIRST_PASS_ONLY']=='1' for _,_,env in spawned)==10


def test_background_review_does_not_start_unbounded_review_hosts(setup,monkeypatch):
    c,index,spawned=queue(setup,monkeypatch,count=18)
    c.agent_settings['review_reserve']=2
    for n in range(13,19):
        d=Path(index[str(n)]['episode_dir'])/'work/clips/clip_01/attempt_01'
        d.mkdir(parents=True);(d/'clip.mp4').write_bytes(b'first video')
        workspaces.save(index[str(n)],'complete')
    for n in (13,14):active(c,n,action='review')
    c.tick()
    assert all(env['NOVEL_FIRST_PASS_ONLY']=='1' for _,_,env in spawned)
    assert len(spawned)==10


def test_completed_video_clears_stale_asset_wait_without_resetting_budget(setup,monkeypatch):
    c,index,_=queue(setup,monkeypatch,count=1)
    row=c.agents['1'];row.update(status='waiting_assets',phase='waiting_assets',waiting_assets=['old.jpeg'])
    d=Path(index['1']['episode_dir'])/'work/clips/clip_01/attempt_01'
    d.mkdir(parents=True);(d/'clip.mp4').write_bytes(b'first video')
    workspaces.save(index['1'],'complete')
    path=Path(row['run_dir'])/'agent-run.json'
    write(path,{'status':'deferred','phase':'waiting_assets','remaining_seconds':321,'no_progress':1})
    assert batch_review.observe(c,row)
    assert row['batch_stage']=='review' and row['phase']=='ready_agent'
    assert not row['waiting_assets']
    assert read_json(path)['remaining_seconds']==321
