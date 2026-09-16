import json
import threading
import time
from novel_manga.dashboard.cache import CachedSnapshot
from novel_manga.util import atomic_write_json


def finish(cache):
    until=time.monotonic()+3
    while cache.building and time.monotonic()<until:time.sleep(.002)
    assert not cache.building


def test_restart_serves_last_snapshot_during_one_background_refresh(tmp_path):
    path=tmp_path/'history.json'
    saved={'scope':['book'],'at':time.time()-400,'data':{'now':'old','novels':[{'id':'book','done':12}]}}
    atomic_write_json(path,saved)
    cache=CachedSnapshot(path,['book'],300)
    entered,release=threading.Event(),threading.Event()
    calls=[]
    def build():
        calls.append(1);entered.set();assert release.wait(3)
        return {'now':'new','novels':[{'id':'book','done':13}]}
    try:
        data,building,error=cache.read(build)
        assert data==saved['data'] and building and error is None
        assert entered.wait(1)
        assert cache.read(build)[0]==saved['data'] and calls==[1]
        assert json.loads(path.read_text())==saved
    finally:release.set()
    finish(cache)
    restarted=CachedSnapshot(path,['book'],300)
    assert restarted.read(lambda:(_ for _ in ()).throw(AssertionError('fresh snapshot must be reused')))[0]['novels'][0]['done']==13
    assert not restarted.building


def test_failed_refresh_keeps_previous_history(tmp_path):
    path=tmp_path/'history.json';saved={'scope':['book'],'at':0,'data':{'done':12}}
    atomic_write_json(path,saved);cache=CachedSnapshot(path,['book'],300)
    cache.read(lambda:(_ for _ in ()).throw(RuntimeError('failed collection')))
    finish(cache)
    assert cache.peek()=={'done':12} and cache.error=='RuntimeError'
    assert json.loads(path.read_text())==saved


def test_snapshot_scope_and_corrupt_cache_do_not_hide_current_book(tmp_path):
    path=tmp_path/'history.json';atomic_write_json(path,{'scope':['other'],'at':time.time(),'data':{'done':99}})
    assert CachedSnapshot(path,['book'],300).peek() is None
    path.write_text('{unfinished')
    cache=CachedSnapshot(path,['book'],300)
    assert cache.peek() is None
    cache.read(lambda:{'done':1});finish(cache)
    assert cache.peek()=={'done':1}


def test_one_process_snapshot_is_shared_by_all_books(tmp_path, monkeypatch):
    import dashboard_service_thin as service
    calls=[];process_rows=[{'pid':1,'args':[],'cwd':str(tmp_path)}]
    monkeypatch.setattr(service,'processes',lambda:calls.append('processes') or process_rows)
    def metrics(novel, *, process_rows):
        calls.append(novel.name)
        assert process_rows is rows
        return {'mode':'repair','status':'paused'}
    rows=process_rows
    monkeypatch.setattr(service,'pipeline_metrics',metrics)
    snapshots=service.DashboardSnapshots(tmp_path,['a','b','c'])
    result=snapshots.attach_live({'novels':[{'id':n} for n in ['a','b','c']]})
    assert calls==['processes','a','b','c']
    assert all(n['pipeline']['status']=='paused' for n in result['novels'])
