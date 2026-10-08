"""Native H3 sampling compatibility without submitting or generating video."""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.repair import history
from novel_manga.application.rendering import flow
from novel_manga.media import cache, generation
from novel_manga.util import atomic_write_json, read_json
from support.render_context import uninitialized_runner


SAMPLING = {'short_edge': 544, 'aspect_ratio': '16:9', 'num_inference_steps': 9,
            'flow_shift': 12.0, 'audio_flow_shift': 3.0}


@pytest.fixture
def case(tmp_path):
    episode = tmp_path / 'book/book_1'
    directory = episode / 'work/clips/c/attempt_01'
    directory.mkdir(parents=True)
    clip = {'clip_id': 'c', 'kind': 'video', 'request_seconds': 5, 'prompt': 'existing words',
            'references': [], 'lines': [], 'prompt_h3_skip': True}
    target = dict(SAMPLING)
    ctx = SimpleNamespace(settings=SimpleNamespace(local_h3_base_url='http://local-h3'),
        provider=SimpleNamespace(sampling_parameters=lambda: dict(target)), feedback={},
        novel_dir=episode.parent, episode_dir=episode, work=episode / 'work', cache_only=False,
        max_attempts=1, _managed_remaining={'c': 0}, black_checks=set(), speech_checks=set(),
        moderation_repair=False)
    saved = {'clip_id': 'c', 'duration': 5, 'prompt': 'existing words', 'references': [],
             'reference_sha256': [], 'repair_take': 0,
             'h3_sampling': {**SAMPLING, 'short_edge': 768}}
    video = directory / 'clip.mp4'
    video.write_bytes(b'existing cached video')
    atomic_write_json(directory / 'request.json', saved)
    atomic_write_json(directory / 'asr.json', {'passed': True, 'issues': []})
    return ctx, clip, directory, video, saved, target


def test_new_h3_request_records_target_sampler_without_changing_existing_fields(case):
    ctx, clip, _, _, _, _ = case
    request, refs, digests, retry = generation.build_request(ctx, clip, 1)
    assert request['h3_sampling'] == SAMPLING
    assert request['duration'] == 5 and request['prompt'] == 'existing words'
    assert request['repair_take'] == request['seed_variant'] == 0
    assert refs == () and digests == [] and retry == ''
    ctx.settings.local_h3_base_url = None
    assert 'h3_sampling' not in generation.build_request(ctx, clip, 1)[0]


def test_passed_768_cache_satisfies_544_without_submission_or_record_rewrite(case, monkeypatch):
    ctx, clip, directory, video, saved, _ = case
    runner = uninitialized_runner()
    runner.context = ctx
    monkeypatch.setattr(runner, 'prescreens', lambda clip: False)
    monkeypatch.setattr(generation, 'submit', lambda *a, **k: pytest.fail('passed cache resubmitted'))
    before = (directory / 'request.json').read_bytes()
    assert runner.generate_clip(clip, 1) == video
    assert (directory / 'request.json').read_bytes() == before
    assert read_json(directory / 'request.json')['h3_sampling']['short_edge'] == 768
    assert ctx._managed_remaining == {'c': 0}
    assert history.generation_counts(ctx.episode_dir) == {}


def test_544_cache_does_not_satisfy_explicit_768_and_cache_only_preserves_files(case):
    ctx, clip, directory, video, saved, target = case
    target['short_edge'] = 768
    saved['h3_sampling']['short_edge'] = 544
    atomic_write_json(directory / 'request.json', saved)
    ctx.cache_only = True
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    assert not cache.cached_take(ctx, clip, 1)
    assert cache.current_video(ctx, clip, 1, directory, (), [], history) is None
    assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == before
    assert video.is_file()


@pytest.mark.parametrize('parameter,value', [('num_inference_steps', 5), ('flow_shift', 8.0),
                                           ('audio_flow_shift', 1.0), ('aspect_ratio', '9:16')])
def test_higher_resolution_does_not_hide_changed_known_sampling(case, parameter, value):
    ctx, clip, directory, _, saved, _ = case
    saved['h3_sampling'][parameter] = value
    atomic_write_json(directory / 'request.json', saved)
    assert not cache.cached_take(ctx, clip, 1)


def test_legacy_size_evidence_retains_unknown_old_nfe_and_original_request(case, monkeypatch):
    ctx, clip, directory, video, saved, _ = case
    saved.pop('h3_sampling')
    atomic_write_json(directory / 'request.json', saved)
    monkeypatch.setattr(cache, '_native_size', lambda path: (1344, 768))
    before = (directory / 'request.json').read_bytes()
    assert cache.cached_take(ctx, clip, 1)
    report = cache.sampling_report(ctx, video)
    assert report['h3_sampling']['short_edge'] == 768
    assert report['h3_sampling']['num_inference_steps'] is None
    assert report['h3_sampling']['flow_shift'] is None
    assert report['h3_sampling_target'] == SAMPLING
    assert (directory / 'request.json').read_bytes() == before


def test_legacy_544_cannot_match_768_target(case, monkeypatch):
    ctx, clip, directory, _, saved, target = case
    saved.pop('h3_sampling')
    atomic_write_json(directory / 'request.json', saved)
    target['short_edge'] = 768
    monkeypatch.setattr(cache, '_native_size', lambda path: (960, 544))
    assert not cache.cached_take(ctx, clip, 1)


def test_actual_task_sampling_wins_over_target_written_before_resume(case):
    ctx, _, directory, video, saved, target = case
    saved['h3_sampling'] = dict(target)
    atomic_write_json(directory / 'request.json', saved)
    actual = {**SAMPLING, 'short_edge': 768}
    atomic_write_json(video.with_suffix('.mp4.task.json'), {'task_id': 'existing-task', 'h3_sampling': actual})
    report = cache.sampling_report(ctx, video)
    assert report == {'h3_sampling': actual, 'h3_sampling_target': SAMPLING}


def test_source_accepted_take_still_checks_sampling_before_reuse(case, monkeypatch):
    ctx, clip, directory, video, saved, target = case
    saved['h3_sampling']['short_edge'] = 544
    atomic_write_json(directory / 'request.json', saved)
    target['short_edge'] = 768
    runner = uninitialized_runner()
    runner.context = ctx
    monkeypatch.setattr(history, 'source_accepted_take', lambda *a: video)
    monkeypatch.setattr(runner, 'analyse_clip', lambda *a: pytest.fail('lower sampling was reused'))
    monkeypatch.setattr(flow.clip_attempts, 'generate_attempt', lambda *a:
                        SimpleNamespace(error=RuntimeError('new request required')))
    monkeypatch.setattr(flow.clip_retries, 'recovery_steps', lambda *a, **kw: [])
    result = runner.process_clip(clip)
    assert result['selected'] is None and 'new request required' in result['error']
    assert ctx._managed_remaining == {'c': 0}


def test_resume_updates_only_current_request_sampling_and_keeps_generation_owner(case, monkeypatch):
    ctx, clip, _, video, _, target = case
    actual = {**SAMPLING, 'short_edge': 768}
    request, refs, _, _ = generation.build_request(ctx, clip, 1)
    owner = {'trial': 7, 'clip_id': 'c', 'task_id': 'existing-task'}
    request['managed_generation'] = copy.deepcopy(owner)
    atomic_write_json(video.with_suffix('.mp4.task.json'), {'task_id': 'existing-task', 'h3_sampling': actual})
    ctx.provider.create_video = lambda *a, **k: video
    monkeypatch.setattr('novel_manga.story.h3.request_issues', lambda *a: [])
    assert generation.submit(ctx, clip, request, video, refs) == video
    assert request['h3_sampling'] == actual and request['h3_sampling_target'] == target
    assert request['managed_generation'] == owner


def test_analysis_reports_selected_native_sampling_separately_from_target(case, monkeypatch):
    ctx, clip, directory, video, _, _ = case
    runner = uninitialized_runner()
    runner.context = ctx
    monkeypatch.setattr(flow.media_analysis, 'analyse_clip', lambda *a:
                        {'passed': True, 'video': str(video), 'issues': []})
    monkeypatch.setattr('novel_manga.application.profiles.speech_gate_result', lambda novel, result, episode: result)
    before = (directory / 'request.json').read_bytes()
    result = runner.analyse_clip(clip, video)
    assert result['h3_sampling']['short_edge'] == 768
    assert result['h3_sampling_target']['short_edge'] == 544
    assert result['passed'] is True
    assert (directory / 'request.json').read_bytes() == before


def test_samplerless_replay_context_has_no_invented_native_parameters(case):
    ctx, clip, _, _, _, _ = case
    del ctx.provider
    assert generation.h3_sampling(ctx) is None
    assert 'h3_sampling' not in generation.build_request(ctx, clip, 1)[0]


@pytest.mark.parametrize('missing', [False, True])
def test_replay_without_settings_preserves_accepted_take_without_probing(case, monkeypatch, missing):
    ctx, _, _, video, _, _ = case
    if missing:
        del ctx.settings
    else:
        ctx.settings = None
    monkeypatch.setattr(cache, '_native_size', lambda path: pytest.fail('replay must not infer production sampling'))
    assert generation.h3_sampling(ctx) is None
    assert cache.sampling_matches(ctx, video=video)
    assert cache.sampling_report(ctx, video) == {}


def test_other_attempt_cache_checks_sampling_and_retains_existing_asr_rule(case):
    ctx, clip, directory, video, saved, target = case
    target['short_edge'] = 768
    saved['h3_sampling']['short_edge'] = 544
    atomic_write_json(directory / 'request.json', saved)
    wanted = directory.parent / 'attempt_02'
    assert cache.other_video(ctx, clip, 2, wanted, (), []) is None
    target['short_edge'] = 544
    assert cache.other_video(ctx, clip, 2, wanted, (), []) == video
    atomic_write_json(directory / 'asr.json', {'passed': False})
    assert cache.other_video(ctx, clip, 2, wanted, (), []) is None


def test_unknown_640_native_dimensions_are_probed_without_inventing_steps(case, monkeypatch):
    ctx, clip, directory, video, saved, _ = case
    saved.pop('h3_sampling')
    atomic_write_json(directory / 'request.json', saved)
    probes = []

    def probe(command):
        probes.append(command)
        return SimpleNamespace(stdout='{"streams":[{"width":1152,"height":640}]}')

    monkeypatch.setattr(cache, 'run', probe)
    assert cache.cached_take(ctx, clip, 1)
    actual = cache.sampling_report(ctx, video)['h3_sampling']
    assert actual['short_edge'] == 640 and actual['num_inference_steps'] is None
    assert probes and all(command[-1] == str(video) for command in probes)


def test_framed_local_provider_exposes_its_actual_sampler():
    from novel_manga.media.adapters import FramedLocalH3
    framed = FramedLocalH3.__new__(FramedLocalH3)
    framed.local = SimpleNamespace(sampling_parameters=lambda: dict(SAMPLING))
    assert framed.sampling_parameters() == SAMPLING


def test_resumed_sampler_is_written_before_completion_without_spending_new_take(case, monkeypatch):
    ctx, clip, directory, video, _, target = case
    video.unlink()
    clip['prompt_h3'] = clip['prompt']
    clip.pop('prompt_h3_skip')
    ctx.clip_plan = {'clips': [clip]}
    ctx.inflight = 0
    ctx._managed_remaining['c'] = 1
    actual = {**SAMPLING, 'short_edge': 768}
    atomic_write_json(video.with_suffix('.mp4.task.json'), {'task_id': 'old-task', 'h3_sampling': actual})

    def resume(prompt, image, output, **kwargs):
        output.write_bytes(b'recovered existing task')
        return output

    ctx.provider.create_video = resume
    runner = uninitialized_runner()
    runner.context = ctx
    monkeypatch.setattr(runner, 'prescreens', lambda clip: False)
    monkeypatch.setattr(runner, 'english_correction_for_new_take', lambda clip: False)
    monkeypatch.setattr('novel_manga.application.profiles.h3_prompt_outdated', lambda *a, **kw: False)
    monkeypatch.setattr('novel_manga.story.h3.request_issues', lambda *a: [])
    monkeypatch.setattr('novel_manga.application.preparation.request_check.before_generation', lambda *a: None)
    monkeypatch.setattr(flow, 'media_duration', lambda *a: 5.0)
    assert runner.generate_clip(clip, 1) == video
    record = read_json(directory / 'request.json')
    assert record['h3_sampling'] == actual and record['h3_sampling_target'] == target
    assert record['seed_variant'] == 0 and record['repair_take'] == 0
    assert clip['_generated'] is False and ctx._managed_remaining == {'c': 1}
    assert history.generation_counts(ctx.episode_dir) == {}
