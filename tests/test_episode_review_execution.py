import copy
import json
from types import SimpleNamespace

import pytest

from novel_manga.application.profiles import plan_fingerprint
from novel_manga.application.rendering import reviewed
from novel_manga.application.review import execution
from novel_manga.util import atomic_write_json, read_json


def material(directory):
    plan = {'clips': [{'clip_id': cid, 'kind': 'video', 'prompt': cid} for cid in ('a', 'b')]}
    media = {'clips': [{'clip_id': cid} for cid in ('a', 'b')], 'status': 'assembled',
             'assembly': {'thin_passed': True}, 'failed_clips': [], 'gate_failed_clips': [],
             'clip_plan_fingerprint': plan_fingerprint(plan), 'review_feedback': {'a': 'existing note'}}
    for name, value in [('clip_plan.json', plan), ('chapter_script.json', {}),
                        ('review_feedback.json', {'a': 'existing note'}), ('thin_media_report.json', media)]:
        atomic_write_json(directory / name, value)
    history = {'trials': [], 'observations': {}, 'prior_generation_counts': {'clips': {'a': 2, 'b': 1}}}
    atomic_write_json(directory / 'repair_history/history.json', history)
    return plan, media


def forbid(*args, **kwargs):
    pytest.fail('review-only must not generate, prepare a repair, or start a trial')


def test_review_only_saves_shared_result_without_rendering_or_changing_notes_and_budget(tmp_path, monkeypatch):
    _, media = material(tmp_path)
    note_bytes = (tmp_path / 'review_feedback.json').read_bytes()
    history_bytes = (tmp_path / 'repair_history/history.json').read_bytes()
    verdict = {'clips': {'a': {'severity': 'fail'}, 'b': {'severity': 'pass'}},
               'flags': ['a: wrong clothing'], 'feedback': {'a': 'correct clothing'}}
    monkeypatch.setattr(execution, 'review_episode', lambda *a, **kw: copy.deepcopy(verdict))
    monkeypatch.setattr(execution.managed, 'prepare', forbid)
    monkeypatch.setattr(execution.managed, 'prepare_request_conflicts', forbid)
    monkeypatch.setattr(execution.history, 'begin_trial', forbid)
    assert execution.review_only(tmp_path) == verdict
    saved = read_json(tmp_path / 'thin_media_report.json')
    assert saved['assembly'] == media['assembly']
    assert saved['quality_review']['remaining'] == ['a']
    assert saved['quality_review']['generated_counts'] == {'a': 2, 'b': 1}
    assert not saved['quality_review']['passed']
    assert saved['quality_review'] == read_json(tmp_path / 'episode_execution.json')
    assert (tmp_path / 'review_feedback.json').read_bytes() == note_bytes
    assert (tmp_path / 'repair_history/history.json').read_bytes() == history_bytes


def test_review_only_keeps_known_legacy_generation_counts_when_updating_summary(tmp_path, monkeypatch):
    _, media = material(tmp_path)
    (tmp_path / 'repair_history/history.json').unlink()
    media['quality_review'] = {'first_pass_generated': {'a': 2}}
    atomic_write_json(tmp_path / 'thin_media_report.json', media)
    monkeypatch.setattr(execution, 'review_episode', lambda *a, **kw:
                        {'clips': {'a': {'severity': 'pass'}, 'b': {'severity': 'pass'}}, 'feedback': {}})
    execution.review_only(tmp_path)
    assert read_json(tmp_path / 'episode_execution.json')['generated_counts'] == {'a': 2}
    execution.review_only(tmp_path)
    assert read_json(tmp_path / 'episode_execution.json')['generated_counts'] == {'a': 2}
    assert read_json(tmp_path / 'repair_history/history.json')['trials'] == []


@pytest.mark.parametrize('case', ['missing', 'error', 'stale_plan', 'stale_notes'])
def test_a_technical_pass_cannot_hide_incomplete_review_or_stale_material(tmp_path, monkeypatch, case):
    plan, media = material(tmp_path)
    rows = {cid: {'severity': 'pass'} for cid in ('a', 'b')}
    if case == 'missing':
        rows.pop('b')
    elif case == 'error':
        rows['b']['severity'] = 'review_error'
    elif case == 'stale_plan':
        plan['clips'][0]['prompt'] = 'new picture'
        atomic_write_json(tmp_path / 'clip_plan.json', plan)
    else:
        atomic_write_json(tmp_path / 'review_feedback.json', {'a': 'new correction'})
    monkeypatch.setattr(execution, 'review_episode', lambda *a, **kw: {'clips': rows, 'feedback': {}})
    execution.review_only(tmp_path)
    result = read_json(tmp_path / 'episode_execution.json')
    assert result['passed'] is False
    assert result['missing_reviews'] == (['b'] if case == 'missing' else [])
    assert result['review_errors'] == (['b'] if case == 'error' else [])
    if case.startswith('stale'):
        assert 'predates' in result['reason']


def test_review_of_an_alternate_take_does_not_replace_current_acceptance(tmp_path, monkeypatch):
    material(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*.json')}
    seen = []
    monkeypatch.setattr(execution, 'review_episode', lambda *a, **kw: seen.append(kw) or {'flags': []})
    execution.review_only(tmp_path, video_name='stale_clip.mp4')
    assert seen[0]['video_name'] == 'stale_clip.mp4'
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*.json')}


def test_cache_miss_does_not_even_adopt_old_history_or_start_review(tmp_path, monkeypatch):
    plan, media = material(tmp_path)
    (tmp_path / 'repair_history/history.json').unlink()
    media['quality_review'] = {'first_pass_generated': {'a': 2}}
    atomic_write_json(tmp_path / 'thin_media_report.json', media)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*.json')}
    monkeypatch.setattr(reviewed, '_review', forbid)
    ctx = SimpleNamespace(episode_dir=tmp_path, clip_plan=plan, cache_only=True)
    result = reviewed.run(SimpleNamespace(context=ctx, run=lambda: {'status': 'cache_miss'}))
    assert result['quality_review']['passed'] is False
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*.json')}


def test_source_acceptance_without_new_render_uses_the_published_verdict(tmp_path, monkeypatch):
    plan, media = material(tmp_path)
    failing = {'clips': {'a': {'severity': 'fail'}, 'b': {'severity': 'pass'}}, 'feedback': {'a': 'suspected error'}}
    passed = {'clips': {'a': {'severity': 'pass'}, 'b': {'severity': 'pass'}}, 'feedback': {}}
    monkeypatch.setattr(reviewed, '_review', lambda *a, **kw: copy.deepcopy(failing))
    monkeypatch.setattr(reviewed.managed, 'candidates', lambda *a: (['a'], {}))
    def prepare(*args):
        atomic_write_json(tmp_path / 'episode_review.json', passed)
        return {'changed': [], 'accepted': ['a'], 'skip_render': True}
    monkeypatch.setattr(reviewed.managed, 'prepare', prepare)
    ctx = SimpleNamespace(episode_dir=tmp_path, clip_plan=plan, cache_only=False)
    result = reviewed.run(SimpleNamespace(context=ctx, run=forbid), repair_existing=True)
    assert result['quality_review']['passed']
    assert result['quality_review']['remaining'] == []
    assert result['quality_review']['generated_counts'] == {'a': 2, 'b': 1}


def test_full_cache_review_stops_without_repair_or_new_trials(tmp_path, monkeypatch):
    plan, media = material(tmp_path)
    monkeypatch.setattr(reviewed, '_review', lambda *a, **kw:
        {'clips': {'a': {'severity': 'fail'}, 'b': {'severity': 'pass'}}, 'feedback': {'a': 'wrong'}})
    monkeypatch.setattr(reviewed.managed, 'candidates', lambda *a: (['a'], {}))
    monkeypatch.setattr(reviewed.managed, 'prepare', forbid)
    monkeypatch.setattr(reviewed.history, 'begin_trial', forbid)
    ctx = SimpleNamespace(episode_dir=tmp_path, clip_plan=plan, cache_only=True)
    result = reviewed.run(SimpleNamespace(context=ctx, run=lambda: media))
    assert not result['quality_review']['passed']
    assert result['quality_review']['generated_counts'] == {'a': 2, 'b': 1}
