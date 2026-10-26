"""Chapter discovery must not rescan the host once for every production range."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from novel_manga.application.production import control, conductor_workers, conductor_state, episode_agents, workspaces
from novel_manga.application.production.conductor_flow import Conductor
from novel_manga.util import atomic_write_json


def process(pid, book, *, mode=15):
    args = ['python', 'thin_batch.py', '--novel-dir', str(book),
            '--chapters', '1-1', '--stage', 'render']
    if mode is not None:
        args += ['--plan-mode', str(mode)]
    return {'pid':pid, 'args':args, 'cwd':str(book.parent)}


def test_300_range_queries_share_one_process_read(tmp_path, monkeypatch):
    scan = Mock(return_value=[])
    monkeypatch.setattr(control, 'processes', scan)
    monkeypatch.setattr(conductor_workers.subprocess, 'run',
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout=''))
    c = SimpleNamespace(novel_dir=tmp_path/'book', procs={})
    for n in range(1, 301):
        assert not conductor_workers.external_running(c, f'--chapters {n}-{n} --stage render', 15)
    assert scan.call_count == 1


def test_external_discovery_keeps_book_mode_and_worker_ownership(tmp_path, monkeypatch):
    book = tmp_path/'book'
    rows = [process(11, tmp_path/'other'), process(12, book, mode=30), process(13, book)]
    rows[1]['args'][2:4] = ['--novel-dir='+str(book)]
    rows[2]['args'][3] = book.name
    monkeypatch.setattr(control, 'processes', lambda: rows)
    c = SimpleNamespace(novel_dir=book, procs={'owned':SimpleNamespace(pid=13, poll=lambda:None)})
    pattern = '--chapters 1-1 --stage render'
    assert not conductor_workers.external_running(c, pattern, 15)
    assert conductor_workers.external_running(c, pattern, 30)
    c.procs = {}
    assert conductor_workers.external_running(c, pattern, 15)


def test_next_tick_refreshes_exited_and_new_external_jobs(tmp_path, monkeypatch):
    book = tmp_path/'book'
    rows = [process(20, book, mode=None)]
    monkeypatch.setattr(control, 'processes', lambda: list(rows))
    c = object.__new__(Conductor)
    c.executor, c.novel_dir, c.procs = 'episode_agent', book, {}
    monkeypatch.setattr(episode_agents, 'tick', lambda owner:
        conductor_workers.external_running(owner, '--chapters 1-1 --stage render', 15))
    assert c.tick()
    rows.clear()
    assert not c.tick()
    rows.append(process(21, book))
    assert c.tick()


def test_workspace_view_is_shared_with_review_and_refreshed_next_tick(tmp_path, monkeypatch):
    episode = tmp_path/'book/book_1'
    row = {'episode':1, 'episode_dir':str(episode), 'novel_dir':str(episode.parent),
           'run_dir':str(tmp_path/'run'), 'source_episode_dir':str(tmp_path/'source/book_1')}
    atomic_write_json(episode/'clip_plan.json', {'clips':[{'clip_id':'clip_01','kind':'video','request_seconds':4}]})
    parent, peer = object.__new__(Conductor), object.__new__(Conductor)
    parent.executor = peer.executor = 'episode_agent'
    chapter = Mock(wraps=workspaces.chapter)
    monkeypatch.setattr(workspaces, 'chapter', chapter)
    monkeypatch.setattr(episode_agents, 'tick', lambda owner:conductor_state.workspace_view(owner,row)['state'])
    assert not parent.tick()['done']
    assert not conductor_state.workspace_view(parent,row)['state']['done']
    video = episode/'work/clips/clip_01/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'completed first-pass video')
    assert not peer.tick(workspace_views=parent._workspace_views)['done']
    assert chapter.call_count == 1
    refreshed = parent.tick()
    assert refreshed['done'] and not refreshed['accepted']
    assert chapter.call_count == 2


@pytest.mark.parametrize('finishes', [False, True])
def test_quiet_tick_sees_last_completion_but_still_stops_on_unchanged_block(finishes, tmp_path, monkeypatch):
    from novel_manga.application.production import conductor_dispatch, batch_review
    c = object.__new__(Conductor)
    c.executor, c.novel_dir, c.novel_id = 'standard', tmp_path/'book', 'book'
    c.procs, c.keys, c.lanes, c.blocks = {}, {}, {}, []
    c.ranges = [{'a':1,'b':1,'plan_mode':15}]
    c.tmp, c.plan_only, c.quota_stop_file, c.dry = tmp_path, False, None, False
    c.cfg, c.log = {'qwen':{},'review':{'backend':'episode_agent'}}, lambda *a:None
    row = {'episode':1,'episode_dir':str(tmp_path/'episode'),'run_dir':str(tmp_path/'run')}
    c.workspaces, c.workspace_index = {'1':row}, 'unused'
    peer = object.__new__(Conductor)
    peer.executor = 'episode_agent'
    c.review_conductor = peer
    completed = False
    def chapter(row):
        return {'n':1,'planned':True,'done':completed,'accepted':False,'unreviewed':completed,
            'runs':0,'blocked':not finishes,'mode':15,'ready':False,'ready_clips':0,
            'in_process_clips':0,'blocked_clips':int(not finishes)}
    def finish(*args):
        nonlocal completed
        completed = finishes
    monkeypatch.setattr(workspaces,'chapter',chapter)
    monkeypatch.setattr(workspaces,'current',lambda row:{'at':1,
        'status':'complete' if completed else 'rendering' if finishes else 'blocked'})
    monkeypatch.setattr(workspaces,'busy',lambda row:False)
    monkeypatch.setattr(conductor_state,'qwen_waiting',lambda c:0)
    monkeypatch.setattr(conductor_state,'recent_lines',lambda *a:0)
    monkeypatch.setattr(conductor_dispatch,'tick_lanes',finish)
    monkeypatch.setattr(conductor_dispatch,'tick_planning',lambda *a:None)
    monkeypatch.setattr(conductor_dispatch,'range_finished',lambda *a:False)
    def review_pending(owner):
        report=batch_review.handoff(row,view=conductor_state.workspace_view(owner,row))
        return bool(report and report['stage']=='review')
    monkeypatch.setattr(episode_agents,'tick',review_pending)
    more = c.tick()
    assert completed is finishes
    assert more is finishes
    if finishes:
        assert batch_review.handoff(row)['stage'] == 'review'
