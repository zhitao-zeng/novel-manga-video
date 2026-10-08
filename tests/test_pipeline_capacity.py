from pathlib import Path
import pytest

from support.episode_conductor import setup, make, write
from support.episode_conductor import configure_pair, configure_native
from novel_manga.util import read_json


def test_full_repair_queue_and_model_host_caps_do_not_block_first_production(setup,monkeypatch):
    root,config,spawned=setup
    configure_native(root,config,monkeypatch,14)
    c=make(config,root)
    for n in range(1,13):
        row=c.agents[str(n)]
        write(Path(row['run_dir'])/'agent-run.json',{'status':'deferred',
            'pipeline_phase':'input_repair','remaining_seconds':300})
    c.tick()
    assert len(spawned)==14
    assert sum(env['NOVEL_PRODUCTION_ONLY']=='1' for _,_,env in spawned)==2
    assert sum(env['NOVEL_PRODUCTION_ONLY']=='0' for _,_,env in spawned)==12
    c.tick()
    assert len(spawned)==14


def test_unstarted_ready_chapters_ignore_stale_repair_tags_and_model_assignment(setup,monkeypatch):
    root,config,spawned=setup
    configure_native(root,config,monkeypatch,6)
    config['novels'][0]['agent'].update(production_parallel=6,parallel=1)
    c=make(config,root)
    for row in c.agents.values():
        row.update(production_lane='repair',model_endpoint='flashnext',issue_retry_started=False)
    c.tick()
    assert len(spawned)==6
    assert all(env['NOVEL_PRODUCTION_ONLY']=='1' for _,_,env in spawned)
    assert {env['NOVEL_AGENT_MODEL'] for _,_,env in spawned}=={'agent-model','qwen-model'}


def test_existing_semantic_repair_is_not_reclassified_as_first_production(setup,monkeypatch):
    from novel_manga.application.production.agent_preparation import normal_work
    root,config,spawned=setup
    configure_native(root,config,monkeypatch,1);c=make(config,root);row=c.agents['1']
    write(Path(row['run_dir'])/'pipeline-input-repair-started.json',{'phase':'input_repair'})
    row.update(production_lane='repair')
    assert not normal_work(c,row)


@pytest.mark.parametrize('planned',[False,True])
def test_repair_filled_workset_reserves_runnable_first_production(setup,monkeypatch,planned):
    root,config,spawned=setup
    configure_native(root,config,monkeypatch,8)
    config['novels'][0]['agent'].update(production_parallel=2,parallel=2,task_pool_size=4)
    if planned:
        for n in range(5,9):
            write(root/f'book/book_{n}/chapter_script.json',{'shots':[{'index':1}]})
            write(root/f'book/book_{n}/clip_plan.json',{'clips':[{'clip_id':'clip_01','references':[]}]})
    c=make(config,root);c.task_pool_episodes=[1,2,3,4]
    for n in range(1,5):
        write(Path(c.agents[str(n)]['run_dir'])/'agent-run.json',{'status':'deferred',
            'pipeline_phase':'input_repair','remaining_seconds':123})
    c.tick()
    assert sum(env['NOVEL_PRODUCTION_ONLY']=='1' for _,_,env in spawned)==2
    assert sum(env['NOVEL_PRODUCTION_ONLY']=='0' for _,_,env in spawned)==2
    assert len(c.task_pool_episodes)==4
    assert read_json(Path(c.agents['4']['run_dir'])/'agent-run.json')['remaining_seconds']==123
    # Both native jobs now require repair; they must not displace the next
    # native jobs or interrupt the two already running repair workers.
    for n in (5,6):
        c.procs[f'agent_{n}'].poll=lambda:0
        write(Path(c.agents[str(n)]['run_dir'])/'agent-run.json',{'status':'deferred',
            'pipeline_phase':'input_repair','remaining_seconds':110})
    old={n:c.agents[str(n)]['pid'] for n in (1,2)}
    c.tick()
    assert {7,8}.issubset(c.task_pool_episodes)
    assert all(c.agents[str(n)]['pid']==pid for n,pid in old.items())


def test_waiting_for_known_bulk_assets_uses_no_host_and_ready_chapter_goes_first(setup,monkeypatch):
    root,config,spawned=setup
    configure_native(root,config,monkeypatch,2)
    settings=config['novels'][0]['agent'];settings.update(production_parallel=1)
    manifest=root/'asset-manifest.json';settings['asset_manifest']=str(manifest)
    rel='series_assets/locations/location_001/establishing.jpeg'
    write(manifest,[{'asset_id':'location_001','path':rel}])
    import os
    write(manifest.with_name('state.json'),{'status':'running','pid':os.getpid(),'results':{}})
    for n in (1,2):
        write(root/f'book/book_{n}/chapter_script.json',{'shots':[{'index':1}]})
        refs=[{'role':'location','asset_id':'location_001','path':rel}] if n==1 else []
        write(root/f'book/book_{n}/clip_plan.json',{'clips':[{'kind':'video','clip_id':'clip_01','references':refs}]})
    c=make(config,root);c.tick()
    assert [name for name,_,_ in spawned]==['agent_2']
    assert c.agents['1']['status']=='waiting_assets' and not c.agents['1'].get('pid')
    card=root/'book'/rel;card.parent.mkdir(parents=True);card.write_bytes(b'image')
    c.procs['agent_2'].poll=lambda:0
    write(Path(c.agents['2']['run_dir'])/'agent-run.json',{'status':'deferred',
        'pipeline_phase':'review','remaining_seconds':200})
    c.tick()
    assert any(name=='agent_1' and env['NOVEL_PRODUCTION_ONLY']=='1' for name,_,env in spawned)
    assert any(name=='agent_2' and env['NOVEL_PRODUCTION_ONLY']=='0' for name,_,env in spawned)


def test_failed_bulk_card_does_not_leave_chapter_waiting_forever(setup,monkeypatch):
    from novel_manga.application.production import agent_preparation
    root,config,spawned=setup
    configure_native(root,config,monkeypatch,1)
    manifest=root/'assets/manifest.json';config['novels'][0]['agent']['asset_manifest']=str(manifest)
    rel='series_assets/locations/location_001/establishing.jpeg'
    write(manifest,[{'asset_id':'location_001','path':rel}])
    write(manifest.with_name('state.json'),{'status':'finished','pending':0,
        'results':{'location_001':{'status':'failed'}}})
    write(root/'book/book_1/chapter_script.json',{'shots':[{'index':1}]})
    write(root/'book/book_1/clip_plan.json',{'clips':[{'clip_id':'clip_01','references':[
        {'role':'location','asset_id':'location_001','path':rel}]}]})
    c=make(config,root)
    assert not agent_preparation.assets_waiting(c,c.agents['1'])
    c.tick()
    assert len(spawned)==1 and c.agents['1']['status']=='working'


def test_image_helper_has_no_outer_wall_timeout_but_planner_remains_bounded(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from novel_manga.application.preparation import actions
    calls=[]
    monkeypatch.setattr(actions.subprocess,'run',lambda args,**kwargs:calls.append(kwargs.get('timeout')) or SimpleNamespace(returncode=0))
    actions.run_tool(['scripts/build_cards_thin.py','--assets','location_001'])
    actions.run_tool(['scripts/plan_chapter_thin.py'])
    assert calls==[None,1200]


def test_hundred_task_workset_refills_after_completion(setup,monkeypatch):
    root,config,spawned=setup
    configure_native(root,config,monkeypatch,105)
    config['novels'][0]['agent']['task_pool_size']=100
    c=make(config,root);c.tick()
    pool=read_json(root/'production/state.json')['task_pool']
    assert len(pool['episodes'])==100 and 101 not in pool['episodes']
    c.procs['agent_1'].poll=lambda:0
    write(Path(c.agents['1']['run_dir'])/'agent-run.json',{'status':'finished','passed':True})
    c.tick();pool=read_json(root/'production/state.json')['task_pool']
    assert len(pool['episodes'])==100 and 1 not in pool['episodes'] and 101 in pool['episodes']


def test_new_ready_work_can_replace_a_pool_member_waiting_for_images(setup,monkeypatch):
    import os
    root,config,spawned=setup
    configure_native(root,config,monkeypatch,2)
    manifest=root/'assets/manifest.json'
    config['novels'][0]['agent'].update(task_pool_size=1,asset_manifest=str(manifest))
    assets=[{'asset_id':f'location_00{n}','path':f'cards/scene{n}.jpeg'} for n in (1,2)]
    write(manifest,assets);write(manifest.with_name('state.json'),{
        'status':'running','pid':os.getpid(),'results':{}})
    for n,asset in enumerate(assets,1):
        write(root/f'book/book_{n}/chapter_script.json',{'shots':[{'index':1}]})
        write(root/f'book/book_{n}/clip_plan.json',{'clips':[{'clip_id':'clip_01',
            'references':[{'role':'location',**asset}]}]})
    c=make(config,root);c.tick();assert not spawned
    card=root/'book'/assets[1]['path'];card.parent.mkdir();card.write_bytes(b'image')
    c.tick()
    assert [row[0] for row in spawned]==['agent_2']
    assert read_json(root/'production/state.json')['task_pool']['episodes']==[2]
