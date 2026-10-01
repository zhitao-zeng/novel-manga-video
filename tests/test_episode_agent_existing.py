"""Existing script integration: only final requests cross the agent boundary."""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.agents.episode import contracts
from novel_manga.application.agents.episode.tools import EpisodeTools
from novel_manga.application.profiles import h3_stamp
from novel_manga.application.repair import history
from novel_manga.util import atomic_write_json, read_json
from support.episode_agent import CATALOG, SOURCES, episode


def prepared(tmp_path, monkeypatch, *, old_english=True):
    run = tmp_path / 'run'
    book = run / 'test-book'
    directory = book / 'test-book_7'
    work = run / 'sandbox'
    directory.mkdir(parents=True)
    (work / 'output').mkdir(parents=True)
    original = tmp_path / 'original/test-book_7'
    original.mkdir(parents=True)
    ctx = SimpleNamespace(project_root=Path(__file__).resolve().parents[1],
                          source_novel_dir=original.parent, source_episode_dir=original,
                          run_dir=run, novel_dir=book, episode_dir=directory, work_dir=work,
                          input_mode='script', target_seconds=120, clip_seconds_max=15)
    script, plan = contracts.adopt(episode(), catalog=CATALOG, source_segments=SOURCES, profile={})
    script['author_extra'] = {'approved': ['retain source and camera']}
    plan['original_bookkeeping'] = {'keep': True}
    prompts = {}
    for clip in plan['clips']:
        clip['generation_seed'] = 1234
        clip['prompt'] = '旧中文请求保留作元数据。'
        prompts[clip['clip_id']] = clip['prompt_h3'].replace('relaxed grip', 'firm grip')
        if not old_english:
            clip.pop('prompt_h3')
            clip.pop('prompt_h3_of')
    for target in (directory, original):
        atomic_write_json(target / 'chapter_script.json', script)
        atomic_write_json(target / 'clip_plan.json', plan)
    atomic_write_json(directory / 'review_feedback.json', {})
    atomic_write_json(book / 'profile.json', {})
    atomic_write_json(work / 'output/requests.json', {'prompts': prompts})

    class Runner:
        def __init__(self):
            self.context = SimpleNamespace(episode_dir=directory)

        def run(self):
            return {'status': 'assembled', 'clips': [], 'failed_clips': [],
                    'gate_failed_clips': [], 'assembly': {'thin_passed': True}}

    monkeypatch.setattr(EpisodeTools, '_runner', lambda self: Runner())
    return ctx, plan, prompts


def test_existing_script_first_requests_preserve_all_prepared_material(tmp_path, monkeypatch):
    ctx, before, prompts = prepared(tmp_path, monkeypatch, old_english=False)
    script_bytes = (ctx.episode_dir / 'chapter_script.json').read_bytes()
    source_bytes = {p.name: p.read_bytes() for p in ctx.source_episode_dir.iterdir()}
    tools = EpisodeTools(ctx)
    checked = tools.dispatch('check')
    assert checked['valid'] is True and checked['clip_count'] == 8
    assert checked['requested_seconds'] == 120
    assert read_json(ctx.episode_dir / 'clip_plan.json') == before

    rendered = tools.dispatch('render')
    assert rendered['ok'] is True and rendered['status'] == 'assembled'
    after = read_json(ctx.episode_dir / 'clip_plan.json')
    expected = copy.deepcopy(before)
    for clip in expected['clips']:
        clip['prompt_h3'] = prompts[clip['clip_id']]
        clip['prompt_h3_of'] = h3_stamp(clip)
    assert after == expected
    assert (ctx.episode_dir / 'chapter_script.json').read_bytes() == script_bytes
    assert {p.name: p.read_bytes() for p in ctx.source_episode_dir.iterdir()} == source_bytes
    assert read_json(ctx.episode_dir / 'review_feedback.json') == {}
    assert read_json(ctx.run_dir / 'prepared.json')['input_mode'] == 'script'
    assert history.generation_counts(ctx.episode_dir) == {}


def test_existing_requests_reject_missing_clips_and_changed_frozen_bindings(tmp_path, monkeypatch):
    ctx, before, prompts = prepared(tmp_path, monkeypatch)
    candidate = dict(prompts)
    mutations = [
        {cid: text for cid, text in candidate.items() if cid != 'clip_01'},
        {**candidate, 'clip_01': candidate['clip_01'].replace('你来啦。', '你回去。')},
        {**candidate, 'clip_01': candidate['clip_01'].replace('<Subject 2> is the person shown in <Picture 3>.',
                                                            '<Subject 2> is the person shown in <Picture 1>.')},
        {**candidate, 'clip_01': candidate['clip_01'].replace('Planned duration: 15 seconds.',
                                                            'Planned duration: 14 seconds.')},
        {**candidate, 'clip_01': candidate['clip_01'].replace('from <Audio 1>.', 'from <Audio 2>.')},
    ]
    for changed in mutations:
        atomic_write_json(ctx.work_dir / 'output/requests.json', {'prompts': changed})
        answer = EpisodeTools(ctx).dispatch('render', {'file': 'output/requests.json'})
        assert answer['ok'] is False and answer['valid'] is False and answer['errors']
        assert read_json(ctx.episode_dir / 'clip_plan.json') == before
    assert not (ctx.run_dir / 'prepared.json').exists()
    assert not (ctx.episode_dir / 'repair_history').exists()


def test_existing_media_requires_protected_repair_and_preserves_budget(tmp_path, monkeypatch):
    ctx, before, prompts = prepared(tmp_path, monkeypatch)
    video = ctx.episode_dir / 'work/clips/clip_01/attempt_01/clip.mp4'
    video.parent.mkdir(parents=True)
    video.write_bytes(b'existing take')
    tools = EpisodeTools(ctx)
    refused = tools.dispatch('render', {'file': 'output/requests.json'})
    assert refused['ok'] is False and 'apply repair decisions' in refused['error']
    atomic_write_json(ctx.episode_dir / 'episode_review.json', {'feedback': {'clip_01': 'current failure'}})
    atomic_write_json(ctx.episode_dir / 'thin_media_report.json', {})
    monkeypatch.setattr('novel_manga.application.repair.publication.publish_candidate',
                        lambda *a, **k: pytest.fail('a changed protected line must not be published'))
    repair = {'decisions': [{'clip_id': 'clip_01', 'action': 'edit', 'reason': 'correct the take',
                            'prompt_h3': prompts['clip_01'].replace('你来啦。', '你回去。')}]}
    atomic_write_json(ctx.work_dir / 'output/repair.json', repair)
    failed = tools.dispatch('apply')
    assert failed['ok'] is False and failed['changed'] == [] and failed['blocked']['clip_01']
    monkeypatch.setattr(history, 'generation_counts', lambda directory: {'clip_01': 3})
    atomic_write_json(ctx.work_dir / 'output/repair.json', {
        'decisions': [{'clip_id': 'clip_01', 'action': 'retake', 'reason': 'another take'}]})
    exhausted = tools.dispatch('apply')
    assert exhausted['ok'] is False and exhausted['blocked'] == {'clip_01': 'effective clip retry budget used'}
    assert exhausted['generation_counts'] == {'clip_01': 3}
    assert read_json(ctx.episode_dir / 'clip_plan.json') == before
    assert read_json(ctx.episode_dir / 'chapter_script.json') == read_json(ctx.source_episode_dir / 'chapter_script.json')
