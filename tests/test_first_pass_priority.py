from pathlib import Path

import pytest

from support.episode_conductor import setup, make, write, configure_native
from novel_manga.application.production import agent_preparation


def configured(setup, monkeypatch, count=3):
    root, config, spawned = setup
    configure_native(root, config, monkeypatch, count)
    config['novels'][0]['agent'].update(first_pass_priority=True, production_parallel=2, parallel=2)
    c = make(config, root)
    return root, c, spawned


def episode(root, c, n, filmed=()):
    row = c.agents[str(n)]
    run = Path(row['run_dir']); directory = run/'book'/f'book_{n}'
    write(directory/'clip_plan.json', {'clips': [
        {'clip_id': cid, 'kind': 'video', 'references': []} for cid in ('clip_01', 'clip_02')]})
    write(run/'agent-run.json', {'status': 'deferred', 'pipeline_phase': 'input_repair', 'remaining_seconds': 400})
    for cid in filmed:
        f = directory/'work/clips'/cid/'attempt_01/clip.mp4'
        f.parent.mkdir(parents=True); f.write_bytes(b'actual video')
    row.update(production_lane='repair', pipeline_phase='input_repair')
    return directory


def test_unfilmed_semantic_work_uses_first_capacity_before_post_render_repair(setup, monkeypatch):
    root, c, spawned = configured(setup, monkeypatch)
    episode(root, c, 1, ['clip_01', 'clip_02'])
    episode(root, c, 2)
    episode(root, c, 3, ['clip_01'])
    c.tick()
    assert [s[0] for s in spawned] == ['agent_2', 'agent_3']
    assert all(s[2]['NOVEL_FIRST_PASS_ONLY'] == '1' for s in spawned)
    assert agent_preparation.lane(c, c.agents['3']) == 'normal'


def test_post_render_queue_runs_when_no_first_pass_is_runnable(setup, monkeypatch):
    root, c, spawned = configured(setup, monkeypatch, count=2)
    for n in (1, 2):
        episode(root, c, n, ['clip_01', 'clip_02'])
    c.tick()
    assert len(spawned) == 2 and all(s[2].get('NOVEL_FIRST_PASS_ONLY') == '0' for s in spawned)


def test_first_pass_assets_only_require_missing_clips(setup, monkeypatch):
    root, c, _ = configured(setup, monkeypatch, count=1)
    directory = episode(root, c, 1, ['clip_01'])
    write(directory/'clip_plan.json', {'clips': [
        {'clip_id': 'clip_01', 'kind': 'video', 'references': [{'role': 'location', 'path': 'cards/absent.jpeg'}]},
        {'clip_id': 'clip_02', 'kind': 'video', 'references': []}]})
    assert agent_preparation.asset_ready(c, c.agents['1'])


def test_inflight_receipt_without_video_is_still_first_work(setup, monkeypatch):
    root, c, _ = configured(setup, monkeypatch, count=1)
    directory = episode(root, c, 1, ['clip_01'])
    write(directory/'work/clips/clip_02/attempt_01/clip.mp4.task.json', {'task_id': 'accepted'})
    assert agent_preparation.normal_work(c, c.agents['1'])


def test_outside_first_work_displaces_idle_repair_even_below_repair_capacity(setup, monkeypatch):
    root, c, spawned = configured(setup, monkeypatch)
    c.agent_settings['task_pool_size'] = 2
    c.task_pool_episodes = [1, 2]
    episode(root, c, 1, ['clip_01', 'clip_02'])
    episode(root, c, 2, ['clip_01', 'clip_02'])
    episode(root, c, 3)
    c.tick(); c.tick()
    assert [s[0] for s in spawned] == ['agent_3']
    assert 3 in c.task_pool_episodes


def test_first_priority_pool_preserves_an_active_repair_host(setup, monkeypatch):
    root, c, _ = configured(setup, monkeypatch)
    c.agent_settings['task_pool_size'] = 2
    c.task_pool_episodes = [1, 2]
    episode(root, c, 1, ['clip_01', 'clip_02'])
    episode(root, c, 2, ['clip_01', 'clip_02'])
    episode(root, c, 3)
    c.agents['1']['pid'] = 999
    rows = agent_preparation.task_pool(c, [c.agents['2'], c.agents['3']], [c.agents['1']])
    assert c.task_pool_episodes == [1, 3] and [r['episode'] for r in rows] == [3]
