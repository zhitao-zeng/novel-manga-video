from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.production import workspaces
from novel_manga.util import atomic_write_json


def workspace(root, n=1):
    run = root / f'run{n}'
    return dict(episode=n, run_dir=str(run), novel_dir=str(run/'book'),
                episode_dir=str(run/f'book/book_{n}'), source_novel_dir=str(root/'book'),
                source_episode_dir=str(root/f'book/book_{n}'), input_mode='script')


def test_h3_translation_failure_does_not_skip_later_clips(tmp_path, monkeypatch):
    from novel_manga.application.production.flow import Batch
    from novel_manga.application.preparation import readiness
    from novel_manga.application.rendering import h3
    from novel_manga.application import profiles as thin_profile
    batch = Batch.__new__(Batch)
    batch.args = SimpleNamespace(first_pass=True)
    batch.rows = {1: {}}
    batch.episode_dir = lambda n: tmp_path
    atomic_write_json(tmp_path/'clip_plan.json', {'clips': [
        {'clip_id': c, 'kind': 'video'} for c in ('01','02','03')]})
    monkeypatch.setattr(readiness, 'inspect_episode', lambda *a, **k: ({}, {}))
    monkeypatch.setattr(thin_profile, 'h3_prompt_outdated', lambda c, *a, **k: c.get('prompt_h3') != 'ready')
    calls = []
    def convert(c, **kwargs):
        calls.append(c['clip_id'])
        if c['clip_id'] == '02':
            return False
        c['prompt_h3'] = 'ready'
        return True
    monkeypatch.setattr(h3, 'convert', convert)
    assert batch.h3_ready(1) is False
    assert calls == ['01', '02', '03']
    assert set(batch.rows[1]['h3_errors']) == {'02'}


@pytest.mark.parametrize('card_failure', [False, True])
def test_partial_preparation_admits_only_fully_checked_clips(tmp_path, monkeypatch, card_failure):
    from novel_manga.application.production import clip_preparation
    from novel_manga.application.preparation import readiness
    row = workspace(tmp_path)
    atomic_write_json(Path(row['episode_dir'])/'clip_plan.json', {'clips': [
        {'clip_id': c, 'kind': 'video'} for c in ('01','02','03')]})
    batch = SimpleNamespace(rows={1: {'h3_errors': {'02': 'conversion failed'}}},
        plan=lambda n: None, prepare_cards=lambda *a, **k: None, h3_ready=lambda *a, **k: False)
    if card_failure:
        def failed_cards(*a,**k):raise OSError('another card service unavailable')
        batch.prepare_cards=failed_cards
    parent = SimpleNamespace(workspaces={'1': row}, args=SimpleNamespace(dry_run=False))
    monkeypatch.setattr(workspaces, 'bootstrap', lambda r: None)
    monkeypatch.setattr(workspaces, 'local_batch', lambda *a: batch)
    monkeypatch.setattr(workspaces, 'asset_waits', lambda *a: [])
    monkeypatch.setattr(readiness, 'inspect_episode', lambda *a, **k: ({}, {}))
    checked = []
    def check(directory, c):
        checked.append(c['clip_id'])
        if c['clip_id'] == '03':
            raise ValueError('actual reference conflict')
    monkeypatch.setattr(clip_preparation, 'preflight', check)
    result = workspaces.prepare(parent, 1)
    assert result['status'] == 'ready'
    assert result['ready_clips'] == ['01']
    assert set(result['blocked']) == {'02'}
    assert result['unchecked_clips'] == ['03']
    assert checked == ['01']


def test_incremental_preparation_does_not_repeat_failed_inputs(tmp_path,monkeypatch):
    from novel_manga.application.production import clip_preparation
    from novel_manga.application.preparation import readiness
    row=workspace(tmp_path);directory=Path(row['episode_dir'])
    atomic_write_json(directory/'clip_plan.json',{'clips':[
        {'clip_id':c,'kind':'video'} for c in ('01','02','03')]})
    calls=[]
    def h3(n,clip_ids=None):
        calls.extend(sorted(clip_ids));return True
    batch=SimpleNamespace(rows={1:{}},plan=lambda n:None,
        prepare_cards=lambda *a,**k:None,h3_ready=h3)
    parent=SimpleNamespace(workspaces={'1':row},args=SimpleNamespace(dry_run=False))
    monkeypatch.setattr(workspaces,'bootstrap',lambda *a:None)
    monkeypatch.setattr(workspaces,'local_batch',lambda *a:batch)
    monkeypatch.setattr(workspaces,'asset_waits',lambda *a:[])
    monkeypatch.setattr(readiness,'inspect_episode',lambda *a,**k:({},{}))
    def check(d,c):
        if c['clip_id']=='01':raise ValueError('confirmed input conflict')
    monkeypatch.setattr(clip_preparation,'preflight',check)
    first=workspaces.prepare(parent,1)
    assert first['ready_clips']==['02'] and first['unchecked_clips']==['03']
    assert calls==['01','02']
    p=directory/'work/clips/02/attempt_01/clip.mp4';p.parent.mkdir(parents=True);p.write_bytes(b'first')
    workspaces.save(row,'needs_preparation',blocked=first['blocked'],unchecked_clips=['03'])
    second=workspaces.prepare(parent,1)
    assert second['ready_clips']==['03'] and calls==['01','02','03']
    assert '01' in second['blocked']


@pytest.mark.parametrize('stale', [False, True])
def test_partial_render_hands_unchecked_or_stale_clips_back_to_preparation(tmp_path,monkeypatch,stale):
    from novel_manga.application.production import execution
    from novel_manga.util import read_json
    row=workspace(tmp_path);d=Path(row['episode_dir'])
    atomic_write_json(d/'clip_plan.json',{'clips':[{'clip_id':cid,'kind':'video'} for cid in ('01','02','03')]})
    workspaces.save(row,'ready',ready_clips=['02'],blocked={'01':['unchanged failure']},unchecked_clips=['03'])
    def run(command,log):
        assert command[-2:]==['--first-clips','02']
        if stale:
            atomic_write_json(d/'thin_media_report.json',{'clips':[{'clip_id':'02','error':'prepared request is stale'}]})
        else:
            p=d/'work/clips/02/attempt_01/clip.mp4';p.parent.mkdir(parents=True);p.write_bytes(b'first')
        return 3,''
    batch=SimpleNamespace(workspace=row,args=SimpleNamespace(dry_run=False),rows={1:{}},run=run)
    monkeypatch.setattr(execution,'render_command',lambda *a:['renderer'])
    workspaces.render_first(batch,1)
    saved=read_json(d/'batch-production.json')
    assert saved['status']=='needs_preparation'
    assert saved['unchecked_clips']==(['02','03'] if stale else ['03'])
    assert saved['blocked']=={'01':['unchanged failure']}
    assert workspaces.current(row) is None


def test_renderer_failure_without_a_report_does_not_starve_unchecked_clips(tmp_path,monkeypatch):
    from novel_manga.application.production import execution
    from novel_manga.util import read_json
    row=workspace(tmp_path);d=Path(row['episode_dir'])
    atomic_write_json(d/'clip_plan.json',{'clips':[{'clip_id':cid,'kind':'video'} for cid in ('01','02')]})
    workspaces.save(row,'ready',ready_clips=['01'],unchecked_clips=['02'])
    batch=SimpleNamespace(workspace=row,args=SimpleNamespace(dry_run=False),rows={1:{}},
                          run=lambda *a:(1,'renderer exited before report'))
    monkeypatch.setattr(execution,'render_command',lambda *a:['renderer'])
    workspaces.render_first(batch,1)
    saved=read_json(d/'batch-production.json')
    assert saved['status']=='needs_preparation' and saved['unchecked_clips']==['02']
    assert 'renderer exited before report' in saved['blocked']['01'][0]


def test_busy_stock_is_reported_as_in_process_not_available(tmp_path, monkeypatch):
    row = workspace(tmp_path)
    atomic_write_json(Path(row['episode_dir'])/'clip_plan.json', {'clips': [
        {'clip_id': '01', 'kind': 'video', 'references': []}]})
    workspaces.save(row, 'ready', ready_clips=['01'])
    monkeypatch.setattr(workspaces, 'busy', lambda r: True)
    result = workspaces.chapter(row)
    assert result['ready_clips'] == 0
    assert result['in_process_clips'] == 1


def test_preflight_uses_final_audio_order_and_renderer_resolved_paths(tmp_path,monkeypatch):
    import wave
    from novel_manga.application.production import clip_preparation
    from novel_manga.application.preparation import request_check
    from novel_manga.media.generation import build_request
    book=tmp_path/'actual';directory=book/'book_1';directory.mkdir(parents=True)
    alias=tmp_path/'alias';alias.symlink_to(book,target_is_directory=True)
    (book/'picture.jpeg').write_bytes(b'current reference')
    for name in ('a','b'):
        with wave.open(str(book/f'{name}.wav'),'wb') as handle:
            handle.setnchannels(1);handle.setsampwidth(2);handle.setframerate(8000);handle.writeframes(bytes(16000))
    clip={'clip_id':'01','request_seconds':5,'prompt':'剧本','prompt_h3':'A voice <Audio 1>\nB voice <Audio 2>',
          'references':[{'role':'character','name':'甲','path':'picture.jpeg'},
                        {'role':'voice','name':'甲','path':'a.wav'}, {'role':'voice','name':'乙','path':'b.wav'}],
          'lines':[{'speaker_name':'乙','text':'这句台词最长'}]}
    monkeypatch.setattr(clip_preparation,'h3_prompt_outdated',lambda *a,**k:False)
    monkeypatch.setattr(clip_preparation,'request_issues',lambda *a:[])
    monkeypatch.setattr(clip_preparation,'worn_reference_pairs',lambda *a:[])
    monkeypatch.setattr(clip_preparation,'risk_acceptances',lambda *a:{'01':{}})
    monkeypatch.setattr(clip_preparation,'render_risks',lambda *a,**k:([],[]))
    calls=[]
    monkeypatch.setattr(request_check,'request_consistency',lambda *a,**k:calls.append(k['request']) or
                        {'consistent':True,'problems':[]})
    clip_preparation.preflight(alias/'book_1',clip)
    ctx=SimpleNamespace(novel_dir=book.resolve(),work=directory/'work',feedback={},_blocked_clips={},
                        settings=SimpleNamespace(local_h3_base_url='pool'),prepared_requests_only=True)
    request=build_request(ctx,clip,1)[0]
    assert request_check.before_generation(ctx,clip,request)['consistent']
    assert len(calls)==1
    assert calls[0]['prompt']=='A voice <Audio 2>\nB voice <Audio 1>'
    assert calls[0]['references']==[str(book/'picture.jpeg')]


def test_per_chapter_renderer_refills_before_slow_chapter_finishes(tmp_path, monkeypatch):
    from support.episode_conductor import pipeline, make
    from novel_manga.application.production import conductor_common, conductor_workers, conductor_state
    (tmp_path/'book').mkdir()
    atomic_write_json(tmp_path/'book/novel.json', {'novel_id': 'book'})
    atomic_write_json(tmp_path/'book/bible_growth.json', {str(i): {} for i in range(1,4)})
    rows = {str(n): workspace(tmp_path, n) for n in (1,2,3)}
    for n, row in rows.items():
        atomic_write_json(Path(row['episode_dir'])/'clip_plan.json', {'policy': 'test-15s',
            'clips': [{'clip_id': '01', 'kind': 'video', 'references': []}]})
        workspaces.save(row, 'blocked' if n == '3' else 'ready', ready_clips=[] if n == '3' else ['01'])
    index = tmp_path/'index.json'; atomic_write_json(index, rows)
    config = pipeline(tmp_path)
    config['novels'][0].update(executor='standard', workspace_index=str(index), blocks_max=0, blocks_min=0)
    config['novels'][0]['ranges'] = [{'chapters': '1-3', 'plan_mode': 15}]
    config['resources']['video_keys']['h3']['parallel'] = 2
    config['defaults']['qwen'] = {'urls': []}
    monkeypatch.setattr(conductor_common, 'REPO', tmp_path)
    monkeypatch.setattr(conductor_workers, 'external_running', lambda *a: False)
    monkeypatch.setattr(conductor_state, 'qwen_waiting', lambda *a: 0)
    spawned = []
    def spawn(c, name, command, extra=None):
        spawned.append(name)
        c.procs[name] = SimpleNamespace(pid=900+len(spawned), poll=lambda: None)
    monkeypatch.setattr(conductor_workers, 'spawn', spawn)
    c = make(config, tmp_path)
    c.tick()
    assert set(spawned) == {'lane_h3_1', 'lane_h3_2'}
    c.procs['lane_h3_2'].poll = lambda: 0
    p = Path(rows['2']['episode_dir'])/'work/clips/01/attempt_01/clip.mp4'
    p.parent.mkdir(parents=True); p.write_bytes(b'raw')
    workspaces.save(rows['3'], 'ready', ready_clips=['01'])
    c.tick()
    assert spawned[-1] == 'lane_h3_3'
    assert c.procs['lane_h3_1'].poll() is None


def test_restarted_coordinator_stays_alive_for_inherited_renderer(tmp_path,monkeypatch):
    import os
    from support.episode_conductor import pipeline,make
    from novel_manga.application.production import conductor_common,conductor_workers,conductor_state,conductor_dispatch
    row=workspace(tmp_path)
    atomic_write_json(tmp_path/'book/novel.json',{'novel_id':'book'})
    atomic_write_json(tmp_path/'book/bible_growth.json',{'1':{}})
    atomic_write_json(Path(row['episode_dir'])/'clip_plan.json',{'policy':'test-15s',
        'clips':[{'clip_id':'01','kind':'video','references':[]}]})
    workspaces.save(row,'rendering',ready_clips=['01'],worker_pid=os.getpid())
    lock=Path(row['source_episode_dir'])/'.render.lock';lock.parent.mkdir(parents=True,exist_ok=True);lock.write_text(str(os.getpid()))
    index=tmp_path/'index.json';atomic_write_json(index,{'1':row})
    config=pipeline(tmp_path)
    config['novels'][0].update(executor='standard',workspace_index=str(index),blocks_max=0,blocks_min=0,
                             ranges=[{'chapters':'1-1','plan_mode':15}])
    config['defaults']['qwen']={'urls':[]}
    monkeypatch.setattr(conductor_common,'REPO',tmp_path)
    monkeypatch.setattr(conductor_workers,'external_running',lambda *a:False)
    monkeypatch.setattr(conductor_workers,'spawn',lambda *a,**k:pytest.fail('duplicated inherited owner'))
    monkeypatch.setattr(conductor_state,'qwen_waiting',lambda *a:0)
    c=make(config,tmp_path)
    assert c.tick() is True
    assert not conductor_dispatch.range_finished(c,c.ranges[0],conductor_state.range_stats(c,c.ranges[0]))
