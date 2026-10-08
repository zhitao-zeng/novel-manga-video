from contextlib import contextmanager
from types import SimpleNamespace

import pytest


def test_model_queue_longer_than_request_budget_still_gets_full_execution_time(monkeypatch):
    from novel_manga.llm import transport
    now=[100.0];calls=[]
    monkeypatch.setattr(transport.time,'monotonic',lambda:now[0])
    @contextmanager
    def slot(*a,**kwargs):
        assert kwargs.get('deadline') is None
        now[0]+=40
        yield
    monkeypatch.setattr(transport,'model_slot',slot)
    client=SimpleNamespace(post=lambda url,**kw:calls.append(kw['timeout']) or
        SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'ok':True}))
    result=transport.post_any(client,['http://model'],{}, {},deadline=110)
    assert result=={'ok':True} and calls==[10]


def test_episode_wait_accounting_merges_overlaps_and_restricts_to_current_run(tmp_path):
    from novel_manga.resource_waits import wait_seconds
    import json
    path=tmp_path/'waits.jsonl'
    path.write_text(''.join(json.dumps(row)+'\n' for row in [
        {'id':'old','start':1}, {'id':'old','end':5},
        {'id':'a','start':10},{'id':'b','start':12},{'id':'a','end':20},{'id':'b','end':25},
        {'id':'c','start':30}]))
    assert wait_seconds(path,since=15,until=35)==15


def test_model_fallback_keeps_one_active_budget_after_queuing(monkeypatch):
    from novel_manga.llm import transport
    import httpx
    now=[100.0];calls=[]
    monkeypatch.setattr(transport.time,'monotonic',lambda:now[0])
    @contextmanager
    def slot(*a,**kwargs):
        now[0]+=40
        yield
    monkeypatch.setattr(transport,'model_slot',slot)
    def post(url,**kwargs):
        calls.append(kwargs['timeout']);now[0]+=3
        if len(calls)==1:raise httpx.ConnectError('unavailable')
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'ok':True})
    result=transport.post_any(SimpleNamespace(post=post),['http://first','http://second'],{}, {},deadline=110)
    assert result=={'ok':True} and calls==[10,7]


def test_helper_wall_limit_excludes_child_queue_wait_but_keeps_execution_limit(tmp_path,monkeypatch):
    import os,subprocess,sys
    from pathlib import Path
    from novel_manga.resource_waits import run_with_active_timeout
    path=tmp_path/'waits.jsonl'
    monkeypatch.setenv('NOVEL_RESOURCE_WAIT_FILE',str(path))
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
    code=('import time; from novel_manga.resource_waits import QueueWait; '
          'q=QueueWait("image"); q.begin(); time.sleep(0.5); q.finish(); time.sleep(0.02)')
    result=run_with_active_timeout([sys.executable,'-c',code],timeout=.35,env=env)
    assert result.returncode==0
    with pytest.raises(subprocess.TimeoutExpired):
        run_with_active_timeout([sys.executable,'-c','import time; time.sleep(1)'],timeout=.15,env=env)
