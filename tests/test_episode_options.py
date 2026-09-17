import ast
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
import novel_manga.application.production.flow as production
import novel_manga.application.production.render as render


def batch_for(tmp_path, *, replan=False):
    batch = object.__new__(production.Batch)
    batch.args = SimpleNamespace(replan=replan, min_chapter_chars=0, dry_run=False, max_redo=1, merge=1, tier='fast', min_seconds=None)
    batch.novel_dir = tmp_path / 'book'; batch.novel_dir.mkdir()
    batch.novel_id, batch.title = 'book', '测试'
    batch.source, batch.bible = tmp_path / 'source.txt', batch.novel_dir / 'story_bible.json'
    batch.rows = {1:{},2:{}}
    batch.notes = {'1':'original episode note','*':'original shared note'}
    batch.plan_status = lambda _: 'planned'
    batch.chapter = lambda _: SimpleNamespace(text_count=1000)
    for n in [1,2]: batch.episode_dir(n).mkdir()
    return batch


def test_replan_override_cannot_change_parallel_chapter_or_original_notes(tmp_path):
    batch=batch_for(tmp_path); entered=Event();release=Event();calls=[]
    original_args, original_notes=vars(batch.args).copy(),dict(batch.notes)
    def run(command,path):
        calls.append(command)
        if 'plan_chapter_thin.py' in command[1]:
            entered.set(); assert release.wait(5)
        else:
            (path.parent/'clip_plan.json').write_text(json.dumps({'totals':{'video_clip_count':1,'estimated_seconds':15}}))
        return 0,''
    batch.run=run
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(batch.plan,1,replan=True,notes='repair-only note')
        assert entered.wait(5)
        try:
            batch.plan(2)
            assert batch.rows[2]['plan']=='kept'
            assert vars(batch.args)==original_args and batch.notes==original_notes
        finally:release.set()
        future.result()
    assert len(calls)==2 and calls[0][calls[0].index('--notes')+1]=='repair-only note'
    assert batch.notes==original_notes


@pytest.mark.parametrize('initial_replan',[False,True])
def test_failed_forced_replan_keeps_options_and_invalidates_only_its_old_plan(tmp_path,initial_replan):
    batch=batch_for(tmp_path,replan=initial_replan)
    original=copy.deepcopy(batch.notes)
    for n in [1,2]:(batch.episode_dir(n)/'clip_plan.json').write_text('{}')
    batch.run=lambda *a:(1,'planning error')
    batch.plan(1,replan=True,notes='one chapter correction')
    assert batch.args.replan is initial_replan and batch.notes==original
    assert not (batch.episode_dir(1)/'clip_plan.json').exists()
    assert (batch.episode_dir(2)/'clip_plan.json').exists()


def test_production_does_not_write_to_shared_command_options():
    root=Path(__file__).resolve().parents[1]
    for name in ['flow.py', 'render.py']:
        for node in ast.walk(ast.parse((root/'src/novel_manga/application/production'/name).read_text())):
            if isinstance(node,ast.Attribute) and isinstance(node.ctx,ast.Store):
                assert not (isinstance(node.value,ast.Attribute) and node.value.attr=='args'), (name,node.lineno)


@pytest.mark.parametrize('initial_replan',[False,True])
def test_moderation_replan_passes_only_episode_overrides(tmp_path, monkeypatch, initial_replan):
    from support.generation import batch_stub, paid_episode
    import novel_manga.application.profiles as thin_profile
    paid_episode(tmp_path)
    batch=batch_stub(tmp_path,monkeypatch,['clips_failed','clips_failed','clips_failed','done'],replan=initial_replan)
    batch.fast=False;batch.profile={};batch.notes={'1':'user note','*':'shared note'}
    original=copy.deepcopy(batch.notes);seen=[]
    monkeypatch.setattr(batch,'moderation_blocked',lambda _:True)
    monkeypatch.setattr(batch,'moderation_targets',lambda _:' target lines')
    monkeypatch.setattr(thin_profile,'load_genre',lambda _: {})
    def plan(chapter, *, replan=None, notes=None):
        seen.append((chapter,replan,notes))
        assert batch.args.replan is initial_replan and batch.notes==original
        batch.rows[chapter]['plan']='planned'
    monkeypatch.setattr(batch,'plan',plan)
    render.render(batch,1)
    assert len(seen)==1 and seen[0][:2]==(1,True) and seen[0][2].endswith(' target lines')
    assert len(batch.commands)==3 and batch.args.replan is initial_replan and batch.notes==original
