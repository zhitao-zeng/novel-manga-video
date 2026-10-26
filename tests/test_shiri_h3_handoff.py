"""Existing footage and chapter routes survive a dual-H3 continuation."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.production.conductor_capacity import compatible
from novel_manga.application.production.conductor_flow import Conductor
from novel_manga.application.production.conductor_workers import key_env
from novel_manga.application.video_channels import channel_environment
from novel_manga.config import Settings
from novel_manga.providers.phanrouter_video import video_payload
from novel_manga.util import atomic_write_json, read_json


def test_equal_duration_lanes_honor_the_registered_chapter_resource():
    row = Conductor._parse_range({'chapters': '36-36', 'plan_mode': 15,
                                 'render_keys': ['h3cloud']})
    assert compatible(None, {'name': 'h3cloud', 'clip_cap': 15}, row)
    assert not compatible(None, {'name': 'h3pool', 'clip_cap': 15}, row)
    assert not compatible(None, {'name': 'h3cloud', 'clip_cap': 30}, row)
    assert compatible(None, {'name': 'h3pool', 'clip_cap': 15}, {'plan_mode': 15})


def test_independent_review_uses_the_same_pinned_resource(tmp_path, monkeypatch):
    from novel_manga.application.production import conductor_common, episode_agents, agent_models
    monkeypatch.setattr(conductor_common, 'REPO', tmp_path)
    monkeypatch.setattr(agent_models, 'configure', lambda conductor: None)
    source = tmp_path / 'book'
    atomic_write_json(source / 'novel.json', {'novel_id': 'book'})
    atomic_write_json(source / 'book_1/chapter_script.json', {'shots': [{'index': 1}]})
    keys = {'h3pool': {'name': 'h3pool', 'model': 'minimax-h3-ref2va-turbo', 'base_url': 'pool'},
            'h3cloud': {'name': 'h3cloud', 'model': 'MiniMax-H3'}}
    conductor = SimpleNamespace(novel_dir=source, tmp=tmp_path / 'production', keys=keys,
        cfg={'agent': {'input_mode': 'script', 'output_root': str(tmp_path / 'episodes')}},
        ranges=[{'a': 1, 'b': 1, 'plan_mode': 15, 'render_keys': ['h3cloud']}])
    episode_agents.configure(conductor)
    assert conductor.agents['1']['key'] == 'h3cloud'


def test_cloud_reference_publish_directory_is_retained_on_resume(tmp_path):
    public = str(tmp_path / 'published')
    resource = {'model': 'MiniMax-H3', 'key_var': 'H3_TEST_KEY', 'clip_cap': 15,
                'asset_publish_dir': public}
    atomic_write_json(tmp_path / 'configs/pipeline.json', {'resources': {'video_keys': {'h3cloud': resource}}})
    episode = tmp_path / 'book/book_1'
    atomic_write_json(episode / 'clip_plan.json', {'clips': []})
    env = channel_environment(tmp_path, 'h3cloud', episode_dir=episode)
    assert env['NOVEL_ASSET_PUBLISH_DIR'] == public
    assert key_env(None, {'name': 'h3cloud', **resource})['NOVEL_ASSET_PUBLISH_DIR'] == public
    assert read_json(episode / 'video-channel.json')['resource']['asset_publish_dir'] == public
    atomic_write_json(tmp_path / 'configs/pipeline.json', {'resources': {'video_keys': {}}})
    assert channel_environment(tmp_path, 'h3cloud', episode_dir=episode)['NOVEL_ASSET_PUBLISH_DIR'] == public


def test_adopted_h3_route_can_prepare_existing_long_takes_without_erasing_them(tmp_path):
    episode = tmp_path / 'book/book_1'
    resource = {'model': 'MiniMax-H3', 'key_var': 'H3_TEST_KEY', 'clip_cap': 15}
    atomic_write_json(episode / 'video-channel.json', {'name': 'h3cloud', 'resource': resource})
    atomic_write_json(episode / 'clip_plan.json', {'clips': [{'clip_id': 'clip_01', 'kind': 'video',
                                                            'request_seconds': 24}]})
    video = episode / 'work/clips/clip_01/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'existing selected legacy take')
    before = (episode / 'clip_plan.json').read_bytes()
    assert channel_environment(tmp_path, 'h3cloud', episode_dir=episode)['NOVEL_VIDEO_MODEL'] == 'MiniMax-H3'
    assert video.read_bytes() == b'existing selected legacy take'
    assert (episode / 'clip_plan.json').read_bytes() == before


def test_new_overlong_cloud_h3_requests_fail_before_any_payment():
    with pytest.raises(ValueError, match='15'):
        video_payload(Settings(video_model='MiniMax-H3'), 'Retain the whole authored line.', None, 24)


def test_globally_admitted_local_pool_can_serve_two_books_but_paid_keys_stay_exclusive():
    from novel_manga.application.production.cli import validate
    data = {'resources': {'video_keys': {'h3pool': {
        'model': 'minimax-h3-ref2va-turbo', 'base_url': 'pool',
        'inflight_dir': '/shared/h3pool', 'clip_cap': 15}}, 'planning_models': {}},
        'novels': [{'id': name, 'title': name, 'active': True,
                    'render_keys': ['h3pool'], 'planning': {},
                    'ranges': [{'chapters': '1-1', 'plan_mode': 15}]} for name in ('a', 'b')]}
    assert not validate(data)
    data['resources']['video_keys']['h3pool'].update(model='MiniMax-H3', base_url='')
    assert any('h3pool' in problem for problem in validate(data))


def test_one_native_book_can_use_short_ticks_without_changing_other_book_schedules(tmp_path):
    from novel_manga.application.configuration import config_for_novel
    data = {'tick_seconds': 90, 'round_gap_seconds': 120, 'novels': [
        {'id': 'native', 'ranges': [], 'tick_seconds': 2, 'round_gap_seconds': 0},
        {'id': 'legacy', 'ranges': []}]}
    native = config_for_novel(data, 'native', root=tmp_path)
    legacy = config_for_novel(data, 'legacy', root=tmp_path)
    assert (native['tick_seconds'], native['round_gap_seconds']) == (2, 0)
    assert (legacy['tick_seconds'], legacy['round_gap_seconds']) == (90, 120)


@pytest.mark.parametrize('old_english', ['', 'stale unused English'])
def test_legacy_chinese_cache_is_available_to_normal_h3_review(tmp_path, monkeypatch, old_english):
    from novel_manga.application.rendering.cached_takes import approved_cached_take
    from novel_manga.application.rendering import clip_analysis
    from novel_manga.media.context import RenderContext
    episode = tmp_path / 'book/book_1'
    attempt = episode / 'work/clips/clip_01/attempt_01'
    attempt.mkdir(parents=True)
    video = attempt / 'clip.mp4'
    video.write_bytes(b'existing approved legacy footage')
    clip = {'clip_id': 'clip_01', 'kind': 'video', 'request_seconds': 24,
            'prompt': '原始中文分镜', 'prompt_h3_skip': True, 'prompt_h3': old_english,
            'prompt_h3_of': 'stale legacy stamp', 'references': [], 'lines': []}
    atomic_write_json(attempt / 'request.json', {'prompt': clip['prompt'], 'duration': 24,
        'references': [], 'reference_sha256': [], 'model': 'sd2.5'})
    atomic_write_json(attempt / 'asr.json', {'passed': True, 'issues': []})
    ctx = RenderContext(novel_dir=episode.parent, episode_dir=episode, work=episode/'work',
                        settings=Settings(video_model='MiniMax-H3'), feedback={})
    monkeypatch.setattr(clip_analysis, 'recheck_speech', lambda ctx, clip, record, video: record)
    monkeypatch.setattr(clip_analysis, 'check_clip_black', lambda ctx, clip, record, video: record)
    assert approved_cached_take(ctx, clip)['video'] == str(video)
    assert video.read_bytes() == b'existing approved legacy footage'


@pytest.mark.parametrize('inherited', [False, True])
def test_native_lane_shares_host_budget_across_ready_ranges(tmp_path, monkeypatch, inherited):
    from novel_manga.application.production import conductor_dispatch, conductor_workers, workspaces
    key = {'name': 'h3', 'model': 'minimax-h3-ref2va-turbo', 'base_url': 'pool',
           'key_var': '', 'clip_cap': 15, 'parallel': 2, 'inflight': {'max': 2}}
    ranges = [{'a': n, 'b': n, 'plan_mode': 15, 'render_keys': ['h3']} for n in (1, 2, 3)]
    c = SimpleNamespace(workspaces={str(n): {'episode': n} for n in (1, 2, 3)},
        keys={'h3': key}, ranges=ranges, procs={}, cfg={}, novel_dir=tmp_path,
        workspace_index=str(tmp_path/'index.json'),
        lanes={'h3': {'range': [1, 1, 15], 'parked_until': 0.0, 'next_round_at': 0.0}})
    monkeypatch.setattr(conductor_dispatch, 'range_finished', lambda *a: False)
    monkeypatch.setattr(conductor_workers, 'alive', lambda *a: False)
    monkeypatch.setattr(conductor_workers, 'external_running', lambda *a: False)
    monkeypatch.setattr(workspaces, 'current', lambda row:
        {'status': 'rendering'} if inherited and row['episode'] in {1, 2} else None)
    spawned = []
    monkeypatch.setattr(conductor_workers, 'spawn', lambda c, name, command, env: spawned.append(name))
    stats = {id(r): {'renderable': [r['a']]} for r in ranges}
    conductor_dispatch.tick_lanes(c, stats)
    assert spawned == ([] if inherited else ['lane_h3_1', 'lane_h3_2'])
