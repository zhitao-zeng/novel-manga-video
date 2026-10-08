import copy
from pathlib import Path

import pytest

from novel_manga.application.agents.episode import local_preparation
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.util import atomic_write_json, read_json
from support.episode_existing import prepared


def local_case(tmp_path, monkeypatch):
    ctx, plan, _ = prepared(tmp_path, monkeypatch, old_english=False)
    atomic_write_json(ctx.work_dir/'input/manifest.json', {'preparation_exception':'input/preparation/exception.json'})
    atomic_write_json(ctx.work_dir/'input/preparation/exception.json', {
        'status':'needs_repair','failure':{'reason':'English prompt incomplete'}})
    return ctx, plan


def test_staging_retains_plan_without_english_and_without_video(tmp_path, monkeypatch):
    ctx, before=local_case(tmp_path,monkeypatch)
    before_script=read_json(ctx.episode_dir/'chapter_script.json')
    local_preparation.stage(ctx)
    assert read_json(ctx.run_dir/'prepared.json')['local_preparation_pending']
    assert read_json(ctx.episode_dir/'clip_plan.json')==before
    assert read_json(ctx.episode_dir/'chapter_script.json')==before_script
    assert not (ctx.episode_dir/'thin_media_report.json').exists()


def test_source_conflict_cannot_submit_video_or_trigger_asset_work(tmp_path, monkeypatch):
    ctx,before=local_case(tmp_path,monkeypatch);local_preparation.stage(ctx)
    monkeypatch.setattr(local_preparation,'audit',lambda directory:{'source_readable':True,'issues':[
        {'kind':'location','stage':1,'problem':'bedroom was bound to lobby','source_quote':'the original room'}]})
    monkeypatch.setattr(local_preparation,'inputs',lambda directory:{'revision':1})
    monkeypatch.setattr(local_preparation,'ensure_assets',lambda *a:pytest.fail('assets before source repair'))
    monkeypatch.setattr(EpisodeTools,'_runner',lambda *a:pytest.fail('video before source repair'))
    result=EpisodeTools(ctx).dispatch('render',{})
    assert not result['ok'] and 'clip_01' in result['blocked']
    assert read_json(ctx.episode_dir/'thin_media_report.json')['blocked_clips']['clip_01']
    assert read_json(ctx.episode_dir/'clip_plan.json')==before


def test_source_check_reuses_same_candidate_but_rechecks_changed_inputs(tmp_path, monkeypatch):
    ctx,_=local_case(tmp_path,monkeypatch);local_preparation.stage(ctx)
    calls=[];revision=[1]
    monkeypatch.setattr(local_preparation,'inputs',lambda directory:{'revision':revision[0]})
    monkeypatch.setattr(local_preparation,'audit',lambda directory:calls.append(revision[0]) or {'source_readable':True,'issues':[]})
    monkeypatch.setattr(local_preparation,'ensure_assets',lambda *a:None)
    monkeypatch.setattr(local_preparation,'ensure_requests',lambda *a:{})
    assert local_preparation.before_render(ctx) is None
    assert local_preparation.before_render(ctx) is None
    assert calls==[1]
    revision[0]=2
    assert local_preparation.before_render(ctx) is None
    assert calls==[1,2]


def test_only_missing_requests_are_translated_and_valid_requests_survive(tmp_path, monkeypatch):
    ctx,before,prompts=prepared(tmp_path,monkeypatch)
    plan=copy.deepcopy(before);plan['clips'][1].pop('prompt_h3')
    atomic_write_json(ctx.episode_dir/'clip_plan.json',plan)
    called=[]
    def convert(clip, **kwargs):
        called.append(clip['clip_id']);clip['prompt_h3']=before['clips'][1]['prompt_h3'];return True
    monkeypatch.setattr(local_preparation,'convert',convert)
    monkeypatch.setattr(local_preparation,'h3_prompt_outdated',lambda clip,note,**kwargs:not clip.get('prompt_h3'))
    assert local_preparation.ensure_requests(ctx)=={}
    after=read_json(ctx.episode_dir/'clip_plan.json')
    assert called==['clip_02']
    assert after['clips'][0]==before['clips'][0]


def test_failed_translation_is_not_repeated_on_the_same_input(tmp_path, monkeypatch):
    ctx, _=local_case(tmp_path,monkeypatch)
    called=[]
    monkeypatch.setattr(local_preparation,'convert',lambda clip,**kw:called.append(clip['clip_id']) or False)
    first=local_preparation.ensure_requests(ctx)
    second=local_preparation.ensure_requests(ctx)
    assert first and second
    assert len(called)==len(set(called))
    assert all('already attempted' in reason for reason in second.values())


def test_missing_card_is_generated_from_its_registered_asset_id(tmp_path, monkeypatch):
    from types import SimpleNamespace
    ctx,_=local_case(tmp_path,monkeypatch)
    ctx.asset_snapshot_dir=ctx.run_dir/'assets'
    ref={'role':'location','name':'卧房','asset_id':'location_007',
         'path':'series_assets/locations/location_007/establishing.jpeg'}
    atomic_write_json(ctx.episode_dir/'clip_plan.json', {'clips':[{'clip_id':'clip_01','references':[ref]}]})
    calls=[]
    def build(command,**kwargs):
        calls.append(command)
        assert command[command.index('--assets')+1]=='location_007'
        picture=ctx.novel_dir/ref['path'];picture.parent.mkdir(parents=True);picture.write_bytes(b'image')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(local_preparation.subprocess,'run',build)
    monkeypatch.setattr('novel_manga.application.agents.episode.input_repair.refresh_inputs',lambda *a:None)
    local_preparation.ensure_assets(ctx)
    assert len(calls)==1
    assert (ctx.asset_snapshot_dir/ref['path']).is_file()
