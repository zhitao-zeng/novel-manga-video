import json
from pathlib import Path

import novel_manga.application.dashboard.metrics as dashboard
from novel_manga.application.repair.history import accepted_clip_material


def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data),encoding='utf-8')


def test_net_delivery_uses_count_changes_and_allows_negative_progress():
    assert dashboard.net_rates([{'at':0,'passed':10}])['15'] is None
    assert dashboard.net_rates([{'at':0,'passed':10},{'at':60,'passed':50}])['15'] is None
    result=dashboard.net_rates([{'at':0,'passed':100},{'at':900,'passed':98}])['15']
    assert result['delta']==-2 and result['per_hour']==-8 and result['complete_window']
    assert not dashboard.net_rates([{'at':0,'passed':100},{'at':7200,'passed':110}])['15']['complete_window']


def test_completed_controller_does_not_raise_a_stale_heartbeat_alarm():
    import subprocess
    source=Path(__file__).resolve().parents[1]/'src/novel_manga/dashboard/static/pipeline.js'
    code="""const fs=require('fs'),vm=require('vm');const c={};vm.createContext(c);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);
const n={title:'book',pipeline:{mode:'repair',status:'complete',controller_alive:false,age_seconds:3600}};
if(c.pipelineHealth(n)!=='ok'||c.pipelineAlerts([n]).includes('需要检查'))process.exit(1);
n.pipeline.status='running';if(c.pipelineHealth(n)!=='bad')process.exit(2);"""
    subprocess.run(['node','-e',code,str(source)],check=True)


def test_sampling_is_monitor_owned_and_deduplicates_source_updates(tmp_path):
    novel=tmp_path/'book';state={'updated_at':'2026-09-15 16:00:00','summary':{'deliverable_precise':100}}
    write(novel/'repair_manager/state.json',state);original=(novel/'repair_manager/state.json').read_bytes()
    dashboard.sample_delivery(novel,state);dashboard.sample_delivery(novel,state)
    assert len(dashboard.read(novel/'monitor_metrics/net_delivery.json'))==1
    assert (novel/'repair_manager/state.json').read_bytes()==original
    state={**state,'updated_at':'2026-09-15 16:05:00','summary':{'deliverable_precise':105}}
    dashboard.sample_delivery(novel,state)
    assert dashboard._NET[str(novel)]['rates']['15']['delta']==5


def test_repair_cost_excludes_legacy_and_duplicate_takes_and_requires_current_review(tmp_path):
    novel=tmp_path/'book';d=novel/'book_1';d.mkdir(parents=True);clips=[];review={};selected=[]
    for cid in ['a','b','c']:
        video=d/f'{cid}.mp4';video.write_bytes(cid.encode())
        clips.append({'clip_id':cid,'prompt':cid})
        review[cid]={'video':str(video),'take':dashboard.take_of(str(video)),
                     'verify':{'verdict':'fine' if cid!='b' else 'obvious'},'story_ok':cid!='b'}
        selected.append({'clip_id':cid,'selected':{'video':str(video)}})
    a={'generated_takes':[{'video':'a','take':[1,2,3]},{'video':'a','take':[4,5,6]}]}
    b={'generated_takes':[{'video':'b','take':[7,8,9]}]}
    render={'clips':{'a':a,'b':b}}
    h={'trials':[{'managed':True,'method':'source_recheck','clips':['a','b','c'],'renders':[render,render]},
                 {'method':'old','clips':['legacy'],'renders':[render]}]}
    write(d/'repair_history/history.json',h);write(d/'clip_plan.json',{'clips':clips})
    write(d/'episode_review.json',{'clips':review});write(d/'thin_media_report.json',{'clips':selected})
    write(d/'source_acceptances.json',{'c':{'video':review['c']['video'],'take':review['c']['take'],'note':'',
                                         'clip':accepted_clip_material(clips[2]),'reference_digests':[]}})
    result=dashboard.repair_details(novel)
    assert (result['tracked'],result['passed'],result['generated'],result['passed_generated'],result['retained'])==(3,2,3,2,1)
    assert result['avg_generations_passed']==1 and result['total_cost_per_passed']==1.5
    # Replacing the selected file invalidates a cached pass even before a new report arrives.
    (d/'c.mp4').write_bytes(b'a replacement video')
    changed=dashboard.episode_details(d)
    assert changed['passed']==1 and changed['retained']==0


def test_pipeline_status_uses_manager_snapshot_and_does_not_schedule(tmp_path):
    novel=tmp_path/'book'
    state={'updated_at':'2026-09-15 16:00:00','status':'running',
           'summary':{'total':20,'deliverable_precise':12,'repair_ready_clips':5,'repair_blocked_clips':2},
           'jobs':[{'id':'j1','kind':'repair','step':1,'status':'running','episodes':[3]},
                   {'id':'j2','kind':'recovery','step':1,'status':'pending','episodes':[4]},
                   {'id':'j3','kind':'repair','step':9,'status':'done','episodes':[5]}]}
    write(novel/'repair_manager/state.json',state)
    write(novel/'profile.json',{'speech_gate':'observe'})
    result=dashboard.pipeline_metrics(novel)
    assert result['speech_gate']=='observe'
    assert result['deliverable']==12 and result['remaining']==8
    assert result['stages']=={'running:prepare':1,'pending:render':1}
    assert len(result['jobs'])==2 and dashboard.read(novel/'repair_manager/state.json')==state
    assert not (novel/'monitor_metrics').exists()
    write(novel/'modelscope_upload.json',{'status':'uploading','remote_episodes':12,'episode_count':20})
    assert dashboard.pipeline_metrics(novel)['modelscope_upload']['status']=='uploading'


def test_resource_capacity_counts_only_available_instances(tmp_path,monkeypatch):
    monkeypatch.setattr(dashboard.time,'time',lambda:10000)
    slots=tmp_path/'slots'
    write(tmp_path/'configs/h3_pool.json',{'slot_dir':str(slots),'slots':2,'resident':[
        {'name':'A','url':'http://127.0.0.1:1001'},
        {'name':'B','url':'http://127.0.0.1:1002'},
        {'name':'C','url':'http://127.0.0.1:1003','enabled':False}]})
    for port,ok in [(1001,True),(1002,False),(1003,True)]:
        write(slots/f'127.0.0.1_{port}'/'health.json',{'at':10000,'ok':ok})
    result=dashboard.resource_snapshot(tmp_path)
    assert result['available_instances']==1 and result['available_slots']==2
    assert len(result['instances'])==3 and result['night_instances']==0


def test_audit_only_metrics_use_queue_scope_and_effective_verdict(tmp_path):
    import sqlite3
    novel=tmp_path/'book';directory=novel/'repair_manager';directory.mkdir(parents=True)
    write(directory/'audit_launch.json',{'workers':8,'model_filter':'minimax-h3-ref2va-turbo','counts':{'excluded_sd2_5':90}})
    write(directory/'audit_status_qwen.json',{'at':'2026-09-15 17:00:00','status':'running'})
    db=sqlite3.connect(directory/'shared_audit.sqlite3')
    db.execute('CREATE TABLE checks(status TEXT,episode INTEGER,result TEXT)')
    db.executemany('INSERT INTO checks VALUES(?,?,?)',[
        ('done',1,json.dumps({'verdict':'fine'})),
        ('done',1,json.dumps({'verdict':'fine','actor_missing':True})),
        ('pending',2,None),('running',3,None),('error',4,None),('superseded',5,None)])
    db.commit();db.close()
    result=dashboard.pipeline_metrics(novel)
    assert result['mode']=='audit' and 'deliverable' not in result
    a=result['audit']
    assert (a['total'],a['checked'],a['passed'],a['flagged'],a['flagged_episodes'])==(6,2,1,1,1)
    assert a['scope']=='仅 H3 片段' and a['workers']==8 and a['excluded_models']=={'sd2_5':90}


def test_sd_audit_counts_models_without_changing_h3_delivery_scope(tmp_path):
    import sqlite3
    novel=tmp_path/'book';directory=novel/'repair_manager/sd_audit';directory.mkdir(parents=True)
    write(novel/'repair_manager/state.json',{'summary':{'total':2,'deliverable_precise':1},'jobs':[]})
    write(directory/'audit_launch.json',{'workers':8,'scope_label':'SD2.0 + SD2.5 审查',
          'model_counts':{'sd2.0':2,'sd2.5':1},'reused_results':{'sd2.0':1}})
    db=sqlite3.connect(directory/'shared_audit.sqlite3')
    db.execute('CREATE TABLE checks(status TEXT,episode INTEGER,result TEXT)')
    db.executemany('INSERT INTO checks VALUES(?,?,?)',[
        ('done',3,json.dumps({'verdict':'fine','generation_model':'sd2.0'})),
        ('done',3,json.dumps({'verdict':'obvious','actor_missing':True,'generation_model':'sd2.5'})),
        ('pending',4,None)])
    db.commit();db.close()
    result=dashboard.pipeline_metrics(novel)
    assert result['mode']=='repair' and result['total']==2 and result['deliverable']==1
    audit=result['sd_audit']
    assert audit['checked']==2 and audit['total']==3 and audit['reused']==1
    assert audit['models']['sd2.0']=={'total':2,'passed':1,'flagged':0,'checked':1}
    assert audit['models']['sd2.5']['flagged']==1


def test_dashboard_block_details_use_production_eligibility(tmp_path,monkeypatch):
    import novel_manga.application.repair.managed as managed
    d=tmp_path/'book'/'book_1';d.mkdir(parents=True)
    write(d/'repair_routing.json',{'c':{'status':'prepared','action':'retake'}})
    monkeypatch.setattr(managed,'candidates',lambda directory,review:([],{'c':'effective clip retry budget used'}))
    row=dashboard.episode_details(d)
    assert row['blocks'][0]['category']=='有效生成次数用尽'


def test_current_metrics_overlay_does_not_wait_for_historical_board_refresh(tmp_path, monkeypatch):
    import time
    import novel_manga.application.dashboard.config as config
    import novel_manga.application.dashboard.service as server
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.setattr(config, 'NOVELS', [{'id':'book','title':'book'}])
    old={'now':'2026-09-15 15:00:00','novels':[{'id':'book','title':'book','delivery':{'deliverable':1}}]}
    cache=server.snapshots().history
    cache.data, cache.at = old, time.time()
    state={'updated_at':'2026-09-15 17:00:00','status':'running','summary':{'total':20,'deliverable_precise':12},'jobs':[]}
    write(tmp_path/'outputs/book/repair_manager/state.json',state)
    first=server.board_snapshot()
    assert first['novels'][0]['pipeline']['deliverable']==12 and first['now']==old['now']
    state['summary']['deliverable_precise']=13
    write(tmp_path/'outputs/book/repair_manager/state.json',state)
    assert server.board_snapshot()['novels'][0]['pipeline']['deliverable']==13
    assert 'pipeline' not in old['novels'][0]


def test_first_board_load_can_show_current_progress_before_history_is_ready(tmp_path,monkeypatch):
    import novel_manga.application.dashboard.config as config
    import novel_manga.application.dashboard.service as server
    monkeypatch.setattr(config,'ROOT',tmp_path)
    monkeypatch.setattr(config,'NOVELS',[{'id':'book','title':'book'}])
    server.snapshots().history.building=True
    write(tmp_path/'outputs/book/repair_manager/state.json',{'summary':{'total':20,'deliverable_precise':12},'jobs':[]})
    result=server.board_snapshot()
    assert result['building'] and result['novels'][0]['pipeline']['deliverable']==12


def test_cold_live_page_never_waits_for_full_book_validation_or_remote_h3(tmp_path,monkeypatch):
    import novel_manga.application.dashboard.config as config
    import novel_manga.application.dashboard.service as server
    import novel_manga.application.dashboard.inventory as inventory
    import novel_manga.application.dashboard.resources as resources
    import novel_manga.dashboard.cache as cache_module
    import pytest
    monkeypatch.setattr(config,'ROOT',tmp_path)
    monkeypatch.setattr(config,'NOVELS',[{'id':'book','title':'book'}])
    for name in ['_novel_status','_episode_inventory','_plan_modes']:
        monkeypatch.setattr(inventory,name,lambda *a,**k:pytest.fail('slow work on HTTP request path'))
    monkeypatch.setattr(resources,'_local_video',lambda:pytest.fail('remote health on HTTP request path'))
    background=[]
    class Thread:
        def __init__(self,**kw):background.append(kw['target'])
        def start(self):pass
    monkeypatch.setattr(cache_module.threading,'Thread',Thread)
    state={'summary':{'total':20,'deliverable_precise':12},'jobs':[]}
    path=tmp_path/'outputs/book/repair_manager/state.json'
    write(path,state)
    first=server.cached_snapshot()
    assert first['novels'][0]['pipeline']['deliverable']==12
    assert first['novels'][0]['history_loading'] and len(background)==1
    state['summary']['deliverable_precise']=13
    write(path,state)
    second=server.cached_snapshot()
    assert second['novels'][0]['pipeline']['deliverable']==13 and len(background)==1
