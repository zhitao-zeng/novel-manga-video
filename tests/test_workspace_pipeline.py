"""Standard batch production reuses isolated episode books and their original leases."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.util import atomic_write_json, read_json


def item(tmp_path, n=1):
    original = tmp_path/'source/book'; run = tmp_path/f'output/book/book_{n}-source'
    return {'episode': n, 'source_novel_dir': str(original),
        'source_episode_dir': str(original/f'book_{n}'), 'run_dir': str(run),
        'novel_dir': str(run/'book'), 'episode_dir': str(run/'book'/f'book_{n}'),
        'input_mode': 'source'}


def test_standard_batch_routes_each_chapter_to_its_own_book_and_keeps_parent_rows(tmp_path, monkeypatch):
    from novel_manga.application.production import workspaces, flow
    rows = {str(n): item(tmp_path, n) for n in (1, 2)}
    parent = SimpleNamespace(workspaces=rows, _local_batches={}, rows={1: {}, 2: {}},
        args=SimpleNamespace(novel_dir='original', workspace_index='index'), env={'SAFE': 'yes'})
    calls = []
    class Batch:
        def __init__(self, args):
            calls.append(args.novel_dir); self.args=args; self.env={}; self.rows={}
    monkeypatch.setattr(flow, 'Batch', Batch)
    parent.batch_factory=Batch
    a, b = workspaces.local_batch(parent, 1), workspaces.local_batch(parent, 2)
    assert a.args.novel_dir == rows['1']['novel_dir'] and b.args.novel_dir == rows['2']['novel_dir']
    assert a is workspaces.local_batch(parent, 1) and a is not b
    assert a.rows is parent.rows and a.env['SAFE'] == 'yes'
    assert a.args.workspace_index is None
    assert a.lock_directory == Path(rows['1']['source_episode_dir'])


def test_preparation_refuses_a_live_original_episode_lease_before_any_work(tmp_path, monkeypatch):
    import os
    from novel_manga.application.production import workspaces
    row = item(tmp_path); d=Path(row['source_episode_dir']);d.mkdir(parents=True)
    (d/'.render.lock').write_text(str(os.getpid()))
    batch = SimpleNamespace(workspaces={'1': row}, rows={1: {}}, args=SimpleNamespace(dry_run=False))
    monkeypatch.setattr(workspaces, 'bootstrap', lambda *a: pytest.fail('modified an owned workspace'))
    answer=workspaces.prepare(batch, 1)
    assert answer['status'] == 'busy'
    assert (d/'.render.lock').read_text() == str(os.getpid())


def test_preparation_receipt_invalidates_when_real_inputs_change(tmp_path):
    from novel_manga.application.production import workspaces
    row=item(tmp_path);d=Path(row['episode_dir'])
    atomic_write_json(d/'clip_plan.json', {'clips':[{'clip_id':'clip_01','kind':'video','references':[]}]})
    atomic_write_json(d/'chapter_script.json', {'shots':[]})
    receipt=workspaces.save(row, 'blocked', error='one bounded failure')
    assert workspaces.current(row)==receipt
    atomic_write_json(d/'chapter_script.json', {'shots':[{'index':1}]})
    assert workspaces.current(row) is None


def test_accepted_reference_risk_returns_first_inputs_to_standard_preparation(tmp_path):
    from novel_manga.application.production import workspaces
    row=item(tmp_path);d=Path(row['episode_dir'])
    atomic_write_json(d/'clip_plan.json',{'clips':[{'clip_id':'clip_01','kind':'video','references':[]}]})
    workspaces.save(row,'blocked',error='wearer risk')
    atomic_write_json(d/'render_risks_accepted.json',{'clips':{'clip_01':{'wearer_card':['甲']}}})
    assert workspaces.current(row) is None


def test_no_risk_file_keeps_existing_production_receipt_valid_on_upgrade(tmp_path):
    from novel_manga.application.production import workspaces
    row=item(tmp_path);d=Path(row['episode_dir'])
    saved=workspaces.save(row,'blocked',error='existing bounded failure')
    saved['inputs'].pop(str(d/'render_risks_accepted.json'),None)
    atomic_write_json(d/'batch-production.json',saved)
    assert workspaces.current(row)==saved


def test_old_raw_video_is_first_complete_but_not_formal_acceptance(tmp_path):
    from novel_manga.application.production import workspaces
    row=item(tmp_path);d=Path(row['episode_dir'])
    atomic_write_json(d/'clip_plan.json', {'clips':[{'clip_id':'clip_01','kind':'video','references':[]}]})
    p=d/'work/clips/clip_01/attempt_01/clip.mp4';p.parent.mkdir(parents=True);p.write_bytes(b'old video')
    status=workspaces.chapter(row)
    assert status['done'] and status['unreviewed'] and not status['accepted']
    assert p.read_bytes()==b'old video'


def test_adopted_agent_plan_uses_recorded_clip_limit_for_standard_lane(tmp_path):
    from novel_manga.application.production import workspaces,runs
    row=item(tmp_path);d=Path(row['episode_dir'])
    plan={'policy':'episode-agent-authored-v1','limits':{'max_clip_seconds':15},
          'clips':[{'clip_id':'clip_01','kind':'video','references':[]}]}
    atomic_write_json(d/'clip_plan.json',plan);workspaces.save(row,'ready',ready_clips=['clip_01'])
    assert workspaces.chapter(row)['mode']==15
    assert runs.clip_plan_mode(plan)==15
    assert runs.clip_plan_mode({'limits':{'max_clip_seconds':30}})==30


def test_prepare_uses_native_steps_once_and_does_not_call_an_agent(tmp_path, monkeypatch):
    from novel_manga.application.production import workspaces
    row=item(tmp_path);d=Path(row['episode_dir']);book=Path(row['novel_dir'])
    atomic_write_json(d/'clip_plan.json', {'clips':[{'clip_id':'clip_01','kind':'video','references':[]}]})
    atomic_write_json(d/'chapter_script.json', {'shots':[]})
    calls=[]
    child=SimpleNamespace(args=SimpleNamespace(dry_run=False),env={},novel_dir=book,
        plan=lambda n:calls.append('plan'),prepare_cards=lambda n,**k:calls.append('cards'),
        h3_ready=lambda n,**k:calls.append('h3') or True,
        episode_dir=lambda n:d,rows={1:{}})
    parent=SimpleNamespace(workspaces={'1':row},args=SimpleNamespace(dry_run=False),rows=child.rows)
    monkeypatch.setattr(workspaces,'bootstrap',lambda *a:calls.append('bootstrap'))
    monkeypatch.setattr(workspaces,'local_batch',lambda *a:child)
    monkeypatch.setattr(workspaces,'asset_waits',lambda *a:[])
    monkeypatch.setattr('novel_manga.application.production.clip_preparation.preflight',lambda *a:None)
    monkeypatch.setattr('novel_manga.application.preparation.readiness.inspect_episode',lambda *a,**kw:(read_json(d/'clip_plan.json'),{}))
    monkeypatch.setattr('novel_manga.application.agents.episode.session.run_session',lambda *a,**kw:pytest.fail('Agent in normal preparation'))
    assert workspaces.prepare(parent,1)['status']=='ready'
    assert calls==['bootstrap','plan','cards','h3']
    assert workspaces.prepare(parent,1)['status']=='ready' and calls==['bootstrap','plan','cards','h3']


def test_standard_coordinator_uses_prepare_blocks_and_prepared_render_lane(tmp_path, monkeypatch):
    from support.episode_conductor import pipeline, make
    from novel_manga.application.production import conductor_common, conductor_workers, conductor_state
    from novel_manga.application.production import workspaces
    root=tmp_path; original=root/'book';original.mkdir()
    atomic_write_json(original/'novel.json', {'novel_id':'book'})
    atomic_write_json(original/'bible_growth.json', {'3':{}})
    rows={str(n):item(root,n) for n in (1,2,3)}
    for row in rows.values():
        row['source_novel_dir']=str(original);row['source_episode_dir']=str(original/f"book_{row['episode']}")
    index=root/'workspaces.json';atomic_write_json(index,rows)
    config=pipeline(root);config['novels'][0].update(executor='standard',workspace_index=str(index),blocks_max=2,blocks_min=2)
    config['novels'][0]['ranges'][0]['plan_mode']=15
    config['defaults']['qwen']={'urls':[]}
    for n in (1,2):
        d=Path(rows[str(n)]['episode_dir']);atomic_write_json(d/'clip_plan.json',{'policy':'test-15s','clips':[{'clip_id':'clip_01','kind':'video','references':[]}]})
        workspaces.save(rows[str(n)],'ready',ready_clips=['clip_01'])
    monkeypatch.setattr(conductor_common,'REPO',root)
    monkeypatch.setattr(conductor_workers,'external_running',lambda *a:False)
    monkeypatch.setattr(conductor_state,'qwen_waiting',lambda *a:0)
    spawned=[]
    def spawn(c,name,command,extra=None):
        spawned.append((name,command,extra));c.procs[name]=SimpleNamespace(pid=900+len(spawned),poll=lambda:None)
    monkeypatch.setattr(conductor_workers,'spawn',spawn)
    c=make(config,root);c.tick()
    render=next(cmd for name,cmd,_ in spawned if name.startswith('lane_'))
    assert '--workspace-index' in render and '--first-pass' in render
    assert render[render.index('--workers')+1]=='0'
    plan=next(cmd for name,cmd,_ in spawned if name.startswith('plan_'))
    assert plan[plan.index('--stage')+1]=='prepare'
    assert c.episode_dir(1)==Path(rows['1']['episode_dir'])
    assert not any('episode_agent.py' in str(cmd) for _,cmd,_ in spawned)


def test_failed_bulk_owner_releases_waiting_preparation(tmp_path, monkeypatch):
    from novel_manga.application.production import workspaces
    row=item(tmp_path);d=Path(row['episode_dir'])
    atomic_write_json(d/'clip_plan.json',{'clips':[{'clip_id':'clip_01','kind':'video','references':[]}]})
    workspaces.save(row,'waiting_assets',waiting_assets=['missing.jpeg'])
    monkeypatch.setattr(workspaces,'asset_waits',lambda *a:['missing.jpeg'])
    assert workspaces.current(row)['status']=='waiting_assets'
    monkeypatch.setattr(workspaces,'asset_waits',lambda *a:[])
    assert workspaces.current(row) is None


def test_first_renderer_requires_ready_receipt_and_preserves_original_lock(tmp_path, monkeypatch):
    from novel_manga.application.production import workspaces
    from novel_manga.application.production import execution
    row=item(tmp_path);d=Path(row['episode_dir'])
    atomic_write_json(d/'clip_plan.json',{'clips':[{'clip_id':'clip_01','kind':'video','references':[]}]})
    calls=[]
    def run(command,log):
        assert (Path(row['source_episode_dir'])/'.render.lock').exists()
        calls.append(command)
        path=d/'work/clips/clip_01/attempt_01/clip.mp4';path.parent.mkdir(parents=True);path.write_bytes(b'first video')
        return 0,''
    batch=SimpleNamespace(workspace=row,args=SimpleNamespace(dry_run=False),rows={1:{}},run=run)
    monkeypatch.setattr(execution,'render_command',lambda *a:['render_clips_thin.py','--workers','0'])
    workspaces.render_first(batch,1)
    assert not calls
    workspaces.save(row,'ready',ready_clips=['clip_01'])
    workspaces.render_first(batch,1)
    assert calls==[['render_clips_thin.py','--workers','0','--first-pass','--max-attempts','1','--first-clips','clip_01']]
    assert not(Path(row['source_episode_dir'])/'.render.lock').exists()
    assert workspaces.current(row)['status']=='complete'
    workspaces.render_first(batch,1)
    assert len(calls)==1


def test_review_handoff_is_once_per_producer_result_and_does_not_renew_time(tmp_path):
    from novel_manga.application.production import workspaces,batch_review
    row=item(tmp_path);d=Path(row['episode_dir']);run=Path(row['run_dir'])
    atomic_write_json(d/'clip_plan.json',{'clips':[{'clip_id':'clip_01','kind':'video','references':[]}]})
    video=d/'work/clips/clip_01/attempt_01/clip.mp4';video.parent.mkdir(parents=True);video.write_bytes(b'first')
    workspaces.save(row,'complete')
    index=tmp_path/'index.json';atomic_write_json(index,{'1':row})
    c=SimpleNamespace(agent_settings={'queue':'batch_review','workspace_index':str(index)},agent_pause=tmp_path/'pause')
    task={'episode':1,'run_dir':str(run),'status':'blocked'}
    original={'status':'blocked','remaining_seconds':321,'agent_seconds':70,'no_progress':2,'attempts':['old']}
    atomic_write_json(run/'agent-run.json',original)
    assert batch_review.observe(c,task)
    saved=read_json(run/'agent-run.json');assert saved['remaining_seconds']==321 and saved['attempts']==['old']
    saved.update(status='finished_with_issues',no_progress=2);atomic_write_json(run/'agent-run.json',saved)
    # Agent's own new media/plan changes do not create a new producer handoff.
    video.write_bytes(b'later agent retake');atomic_write_json(d/'chapter_script.json',{'changed':'agent'})
    assert batch_review.observe(c,task)
    assert read_json(run/'agent-run.json')==saved


@pytest.mark.parametrize('status,remaining', [('paused',100),('blocked',0),('finished',100)])
def test_review_handoff_keeps_pauses_and_exhausted_or_accepted_work(tmp_path,status,remaining):
    from novel_manga.application.production import workspaces,batch_review
    row=item(tmp_path);run=Path(row['run_dir']);d=Path(row['episode_dir'])
    atomic_write_json(d/'clip_plan.json',{'clips':[]});workspaces.save(row,'blocked',error='failed normal planning')
    saved={'status':status,'remaining_seconds':remaining,'passed':status=='finished'}
    atomic_write_json(run/'agent-run.json',saved)
    index=tmp_path/'index.json';atomic_write_json(index,{'1':row})
    c=SimpleNamespace(agent_settings={'queue':'batch_review','workspace_index':str(index)},agent_pause=tmp_path/'pause')
    batch_review.observe(c,{'episode':1,'run_dir':str(run),'status':status})
    assert read_json(run/'agent-run.json')==saved


@pytest.mark.parametrize('production,review_more,expected_more,expected_status', [
    ('blocked',True,True,'running'),('waiting_assets',False,True,'waiting_assets'),
    ('complete',False,False,'blocked')])
def test_parent_completion_includes_review_and_asset_dependencies(tmp_path,monkeypatch,production,review_more,expected_more,expected_status):
    from support.episode_conductor import pipeline,make
    from novel_manga.application.production import workspaces,batch_review,conductor_common,conductor_workers,conductor_state
    root=tmp_path;original=root/'book';original.mkdir();atomic_write_json(original/'novel.json',{'novel_id':'book'})
    atomic_write_json(original/'bible_growth.json',{'1':{}})
    row=item(root);row.update(source_novel_dir=str(original),source_episode_dir=str(original/'book_1'))
    d=Path(row['episode_dir']);atomic_write_json(d/'clip_plan.json',{'policy':'test-15s','clips':[{'clip_id':'clip_01','kind':'video','references':[]}]})
    if production=='complete':
        p=d/'work/clips/clip_01/attempt_01/clip.mp4';p.parent.mkdir(parents=True);p.write_bytes(b'raw')
    workspaces.save(row,production)
    monkeypatch.setattr(workspaces,'asset_waits',lambda *a:['pending'] if production=='waiting_assets' else [])
    index=root/'index.json';atomic_write_json(index,{'1':row})
    config=pipeline(root);config['novels'][0].update(executor='standard',workspace_index=str(index),blocks_max=0,blocks_min=0)
    config['novels'][0]['ranges']=[{'chapters':'1-1','plan_mode':15}];config['defaults']['qwen']={'urls':[]}
    monkeypatch.setattr(conductor_common,'REPO',root)
    monkeypatch.setattr(conductor_workers,'external_running',lambda *a:False)
    monkeypatch.setattr(conductor_state,'qwen_waiting',lambda *a:0)
    monkeypatch.setattr(batch_review,'tick',lambda c,*a:setattr(c,'review_more',review_more))
    c=make(config,root);c.blocks[0]['done']=True
    assert c.tick() is expected_more
    assert read_json(root/'production/state.json')['status']==expected_status


def test_valid_blocked_plan_is_adopted_for_agent_input_repairs(tmp_path,monkeypatch):
    from support.episode_existing import prepared
    from novel_manga.application.production import workspaces
    from novel_manga.application.agents.episode.tools import EpisodeTools
    ctx,plan,_=prepared(tmp_path,monkeypatch);ctx.input_mode='source'
    row={'episode':7,'run_dir':str(ctx.run_dir),'novel_dir':str(ctx.novel_dir),'episode_dir':str(ctx.episode_dir),
         'source_novel_dir':str(ctx.source_novel_dir),'source_episode_dir':str(ctx.source_episode_dir),'input_mode':'source'}
    atomic_write_json(ctx.run_dir/'prepared.json',{'repair_round_start':4,'ready':False})
    atomic_write_json(ctx.episode_dir/'repair_budget_grants.json',{'clip_01':{'limit':2,'reason':'existing'}})
    before=(ctx.episode_dir/'repair_budget_grants.json').read_bytes()
    workspaces.save(row,'blocked',error='one request needs correction')
    assert read_json(ctx.run_dir/'prepared.json')['repair_round_start']==4
    assert read_json(ctx.work_dir/'input/clip_plan.json')==plan
    assert read_json(ctx.work_dir/'input/chapter_script.json')==read_json(ctx.episode_dir/'chapter_script.json')
    assert (ctx.episode_dir/'repair_budget_grants.json').read_bytes()==before
    (ctx.run_dir/'prepared.json').unlink()
    workspaces.save(row,'blocked',error='valid native plan, missing card')
    assert (ctx.run_dir/'prepared.json').exists()
    (ctx.run_dir/'source.txt').write_text('原文：甲在房间。')
    atomic_write_json(ctx.novel_dir/'story_bible.json',{'characters':[{'name':'甲'},{'name':'乙'}]})
    atomic_write_json(ctx.work_dir/'output/repair.json',{'decisions':[{'clip_id':'clip_01','action':'input',
        'reason':'correct the location','source_quote':'原文','shots':[{'index':1,'location':'房间'}]}]})
    result=EpisodeTools(ctx).check({'file':'output/repair.json'})
    assert result['valid'] and result['kind']=='repair_arguments'


def test_corrected_first_inputs_return_to_standard_before_agent_render(tmp_path,monkeypatch):
    from support.episode_existing import prepared
    from novel_manga.application.production import workspaces
    from novel_manga.application.agents.episode.tools import EpisodeTools
    ctx,plan,_=prepared(tmp_path,monkeypatch)
    row={'episode':7,'run_dir':str(ctx.run_dir),'novel_dir':str(ctx.novel_dir),'episode_dir':str(ctx.episode_dir),
         'source_novel_dir':str(ctx.source_novel_dir),'source_episode_dir':str(ctx.source_episode_dir),'input_mode':'script'}
    atomic_write_json(ctx.work_dir/'input/manifest.json',{'production_mode':'batch_review','agent_phase':'input_repair'})
    workspaces.save(row,'blocked',error='old input defect')
    plan['repair_note']='source-checked correction'
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    monkeypatch.setattr(EpisodeTools,'_runner',lambda *a:pytest.fail('Agent stole standard first production'))
    result=EpisodeTools(ctx).render({})
    assert result['ok'] and result['pipeline_pending']
    assert workspaces.current(row) is None
