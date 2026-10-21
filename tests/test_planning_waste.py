"""A quota failure stops dispatch; chapter scans are reused without hiding real changes."""
import json
import threading
from types import SimpleNamespace

import pytest

from novel_manga.application.production import flow, reports
from novel_manga.application.identity import bible_growth
from novel_manga.models.bible import Character, StoryBible
from novel_manga.util import atomic_write_json


def batch(tmp_path, monkeypatch):
    result = object.__new__(flow.Batch)
    result.args = SimpleNamespace(plan_parallel=1, dry_run=False, replan=False,
        min_chapter_chars=0, max_redo=5, merge=1, tier=None, min_seconds=0)
    result.novel_dir = tmp_path/'book'
    result.novel_id = 'book'
    result.source = tmp_path/'source.txt'
    result.title = '书'
    result.bible = result.novel_dir/'story_bible.json'
    result.notes = {}
    result.rows = {n: {} for n in [1, 2, 3]}
    result.planning_stop = threading.Event()
    result.chapter = lambda n: SimpleNamespace(text_count=1000)
    result.grow = lambda n: None
    monkeypatch.setattr(reports, 'volume_checkpoint', lambda *a: None)
    return result


def test_quota_failure_stops_queued_chapters_and_second_pass(tmp_path, monkeypatch):
    result = batch(tmp_path, monkeypatch)
    calls = []
    result.run = lambda *a: calls.append(a) or (1, "httpx.HTTPStatusError: 402 Payment Required")
    result.plan_stage([1, 2, 3])
    assert len(calls) == 1
    assert all(row['plan'] == 'blocked' for row in result.rows.values())
    assert all(row['blocked_reason'] == 'insufficient_credits' for row in result.rows.values())


def test_completed_plan_is_kept_after_quota_failure(tmp_path, monkeypatch):
    result = batch(tmp_path, monkeypatch)
    atomic_write_json(result.episode_dir(3)/'clip_plan.json', {'paid': 'existing plan'})
    result.run = lambda *a: (1, "httpx.HTTPStatusError: 402 Payment Required")
    result.plan_stage([1, 2, 3])
    assert result.rows[3]['plan'] == 'kept'
    assert json.loads((result.episode_dir(3)/'clip_plan.json').read_text()) == {'paid': 'existing plan'}


def test_attempted_forced_replan_does_not_republish_its_superseded_plan_on_402(tmp_path, monkeypatch):
    result = batch(tmp_path, monkeypatch)
    result.args.replan = True
    old = result.episode_dir(1)/'clip_plan.json'
    atomic_write_json(old, {'superseded': True})
    result.run = lambda *a: (1, 'httpx.HTTPStatusError: 402 Payment Required')
    result.plan(1)
    assert result.rows[1]['plan'] == 'blocked'
    assert not old.exists()


def test_eight_inflight_chapters_finish_but_no_more_are_dispatched_after_402(tmp_path, monkeypatch):
    result = batch(tmp_path, monkeypatch)
    result.args.plan_parallel = 8
    result.rows = {n: {} for n in range(1, 65)}
    started = []
    barrier = threading.Barrier(8)
    def run(command, path):
        chapter = int(path.parent.name.rsplit('_', 1)[1])
        if command[1].endswith('plan_chapter_thin.py'):
            started.append(chapter)
            barrier.wait(timeout=5)
            if chapter == 1:
                return 1, 'httpx.HTTPStatusError: 402 Payment Required'
            assert result.planning_stop.wait(5)
        else:
            atomic_write_json(path.parent/'clip_plan.json', {
                'totals': {'video_clip_count': 1, 'estimated_seconds': 15}})
        return 0, ''
    result.run = run
    result.plan_stage(list(result.rows))
    assert set(started) == set(range(1, 9))
    assert sum(r['plan'] == 'planned' for r in result.rows.values()) == 7
    assert sum(r['plan'] == 'blocked' for r in result.rows.values()) == 57


def test_workspace_planners_share_the_parent_quota_stop(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from novel_manga.application.production import workspaces
    parent = batch(tmp_path, monkeypatch)
    parent.workspaces = {str(n): {} for n in parent.rows}
    children = {n: batch(tmp_path, monkeypatch) for n in parent.rows}
    commands = []
    for child in children.values():
        child.rows = parent.rows
        child.run = lambda *a: commands.append(a) or (1, 'httpx.HTTPStatusError: 402 Payment Required')
    @contextmanager
    def lease(*args):
        yield True
    monkeypatch.setattr(workspaces, 'lease', lease)
    monkeypatch.setattr(workspaces, 'bootstrap', lambda *a: None)
    monkeypatch.setattr(workspaces, 'local_batch', lambda parent, n: children[n])
    parent.plan_stage([1, 2, 3])
    assert len(commands) == 1
    assert parent.planning_stop.is_set()
    assert all(row['plan'] == 'blocked' for row in parent.rows.values())


def test_wrapped_quota_failure_is_not_hidden_by_the_final_exception(tmp_path, monkeypatch):
    result = batch(tmp_path, monkeypatch)
    result.env = {}
    path = tmp_path/'plan.log'
    path.write_text('old unrelated error\n')
    def run(command, *, stdout, **kwargs):
        stdout.write("httpx.HTTPStatusError: Client error '402 Payment Required'\n")
        stdout.write('ValueError: first-pass outline incomplete: HTTPStatusError\n')
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(flow.subprocess, 'run', run)
    code, reason = result.run(['unused'], path)
    assert code == 1 and '402 Payment Required' in reason


def test_repeated_growth_reuses_names_locations_and_empty_props(tmp_path, monkeypatch):
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='2d', palette='蓝',
        style_fingerprint='test', characters=[Character(name='甲', appearance='青年', wardrobe='白衣')], locations=[])
    atomic_write_json(tmp_path/'story_bible.json', bible.model_dump(mode='json'))
    atomic_write_json(tmp_path/'profile.json', {'props': True})
    calls = []
    services = bible_growth.service_entity_extraction
    monkeypatch.setattr(services, 'extract_names', lambda *a: calls.append('names') or [])
    monkeypatch.setattr(services, 'extract_locations', lambda *a: calls.append('locations') or [])
    monkeypatch.setattr(services, 'extract_props', lambda *a: calls.append('props') or [])
    for _ in range(2):
        bible_growth.grow_bible(tmp_path, '甲在屋中。', 1)
    assert calls == ['names', 'locations', 'props']
    bible_growth.grow_bible(tmp_path, '甲走到院外。', 1)
    assert calls == ['names', 'locations', 'props'] * 2
