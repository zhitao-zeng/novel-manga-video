import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.review import independent, final_cut, final_dispute_review
from novel_manga.util import atomic_write_json, read_json
from tests.test_independent_final_review import setup


def current_case(tmp_path, monkeypatch):
    runner, media, final, raw = setup(tmp_path, monkeypatch)
    independent.register(tmp_path, media, raw)
    final = independent.combine(tmp_path, final)
    atomic_write_json(tmp_path/'final_cut_review.json', final)
    return media, final


def resolution(final, findings, *, extra=False):
    image = final['frames'][0]['path']
    return {'complete': True, 'passed': not extra, 'errors': [],
            'context': final['context'], 'observations': ['actual current phone absent'],
            'frames': final['frames'], 'issues': ([{
                'id': 'watch', 'clip_ids': ['a'], 'problem': 'unapproved smart watch',
                'source_basis': 'bare wrist', 'evidence': 'watch visible', 'frame_paths': [image]}] if extra else []),
            'prior_findings': [{'id': row['id'], 'status': 'resolved',
                                'evidence': 'only hand and rod in current delivered movie',
                                'frame_paths': [image]} for row in findings]}


def test_dispute_rechecks_current_movie_once_preserving_other_failures_and_original_reports(tmp_path, monkeypatch):
    from novel_manga.application.review import final_dispute
    media, final = current_case(tmp_path, monkeypatch)
    other = {**copy.deepcopy(final['issues'][0]), 'id': 'clothes', 'problem': 'wrong clothes'}
    final['automatic_review'].update(passed=False, issues=[other])
    final['issues'].append(other)
    atomic_write_json(tmp_path/'final_cut_review.json', final)
    original = (tmp_path/'independent_review.json').read_bytes()
    atomic_write_json(tmp_path/'repair_history/history.json', {'prior_generation_counts': {'clips': {'a': 4}}})
    history = (tmp_path/'repair_history/history.json').read_bytes()
    calls = []
    def observe(directory, current, *, dispute):
        calls.append(dispute)
        assert current['context'] == final['context']
        assert dispute['reason'] == 'current final JPEG contains no phone'
        return resolution(current, dispute['findings'], extra=True)
    monkeypatch.setattr(independent, 'observe', observe)
    decision = {'reason': 'current final JPEG contains no phone', 'evidence': ['current JPEG'], 'finding_ids': ['phone']}
    result = final_dispute_review.adjudicate(tmp_path, 'a', decision)
    saved = read_json(tmp_path/'final_cut_review.json')
    assert result['review_changed'] and not saved['passed']
    assert {i['id'] for i in saved['issues']} == {'clothes', 'watch'}
    assert (tmp_path/'independent_review.json').read_bytes() == original
    assert (tmp_path/'repair_history/history.json').read_bytes() == history
    assert final_cut.current(tmp_path, media, saved)
    assert final_dispute.apply(tmp_path, final)['issues'] == saved['issues']
    with pytest.raises(ValueError, match='already'):
        final_dispute_review.adjudicate(tmp_path, 'a', decision)
    assert len(calls) == 1
    archive = next((tmp_path/'work/final_disputes').glob('*/record.json'))
    assert read_json(archive)['original_review'] == final
    newer = copy.deepcopy(final); newer['context']['movie'] = ['different movie']
    assert final_dispute.apply(tmp_path, newer)['issues'] == newer['issues']


@pytest.mark.parametrize('failure', ['incomplete', 'missing_disposition', 'stale'])
def test_invalid_or_stale_dispute_never_clears_findings_and_is_bounded(tmp_path, monkeypatch, failure):
    from novel_manga.application.review import final_dispute
    media, final = current_case(tmp_path, monkeypatch)
    def observe(directory, current, *, dispute):
        answer = resolution(current, dispute['findings'])
        if failure == 'incomplete': answer['complete'] = False
        if failure == 'missing_disposition': answer['prior_findings'] = []
        if failure == 'stale': Path(final['movie']).write_bytes(b'changed during recheck')
        return answer
    monkeypatch.setattr(independent, 'observe', observe)
    with pytest.raises(ValueError):
        final_dispute_review.adjudicate(tmp_path, 'a', {'reason': 'not visible', 'evidence': ['frame']})
    assert read_json(tmp_path/'final_cut_review.json') == final
    assert final_dispute.apply(tmp_path, final)['issues'] == final['issues']
    if failure != 'stale':
        monkeypatch.setattr(independent, 'observe', lambda *a, **k: pytest.fail('second arbitration'))
        with pytest.raises(ValueError, match='already'):
            final_dispute_review.adjudicate(tmp_path, 'a', {'reason': 'try again', 'evidence': ['frame']})


def test_dispute_sandbox_bypasses_same_context_cache_but_does_not_replace_independent_report(tmp_path, monkeypatch):
    media, final = current_case(tmp_path, monkeypatch)
    original = (tmp_path/'independent_review.json').read_bytes()
    config = {'runs_root': str(tmp_path/'runs'), 'extra_args': [], 'repair_extra_body': {}}
    findings = [copy.deepcopy(final['issues'][0])]
    def execute(run, *, config, timeout):
        work = Path(config['runs_root'])/run
        packet = read_json(work/'input/review.json')
        assert packet['dispute']['reason'] == 'phone absent from current export'
        frame = packet['primary_frames'][0]['path']
        answer = resolution(final, findings)
        answer['observations'] = [{'frame_path': frame, 'observation': 'no phone visible'}]
        answer['prior_findings'][0]['frame_paths'] = [frame]
        atomic_write_json(work/'output/review.json', answer)
        return SimpleNamespace(ok=True, produced=['review.json'], directory=work/'attempts/one')
    monkeypatch.setattr(independent, 'run_agent', execute)
    result = independent.observe(tmp_path, final, config=config,
        dispute={'reason': 'phone absent from current export', 'findings': findings, 'evidence': ['current JPEG']})
    assert result['complete'] and result['passed']
    assert (tmp_path/'independent_review.json').read_bytes() == original


def test_managed_final_keep_routes_to_independent_dispute_without_native_keep_or_generation(tmp_path, monkeypatch):
    from tests.test_repair_agent import configured
    from novel_manga.application.repair import agent, managed, source_recheck, history
    from novel_manga.application.review import final_dispute
    directory, clips, review = configured(tmp_path)
    review = read_json(directory/'episode_review.json')
    review['clips']['a']['final_cut'] = {'issues': [{'problem': 'phone'}]}
    atomic_write_json(directory/'episode_review.json', review)
    monkeypatch.setattr(agent, 'propose', lambda *a, **k: {'action': 'keep', 'reason': 'no phone in final frame', 'evidence': ['JPEG']})
    monkeypatch.setattr(source_recheck, 'prepare_source_recheck', lambda *a: pytest.fail('native keep is not arbitration'))
    seen = []
    monkeypatch.setattr(final_dispute_review, 'adjudicate', lambda d, cid, decision: seen.append(cid) or {'review_changed': True, 'changed': []})
    result = managed.prepare_agent_batch(directory, ['a'])
    assert seen == ['a'] and result['review_changed'] and result['skip_render']
    assert result['changed'] == [] and result['accepted'] == []
    assert not history.load(directory)['trials']


def test_review_changed_continues_final_review_without_render(tmp_path, monkeypatch):
    from tests.test_final_cut_loop import fixture, verdict
    from novel_manga.application.rendering import reviewed
    runner, media, movie = fixture(tmp_path, monkeypatch); events = []
    monkeypatch.setattr(reviewed.managed, 'candidates', lambda d, r: (list(r['feedback']), {}))
    monkeypatch.setattr(reviewed.managed, 'prepare', lambda *a: {'changed': [], 'review_changed': True, 'skip_render': True})
    runner.run = lambda: pytest.fail('arbitration must not render')
    def final(report):
        events.append('final'); return verdict(movie, len(events) > 1)
    result = reviewed.run(runner, repair_existing=True, final_review=final)
    assert events == ['final', 'final'] and result['quality_review']['passed']


def test_exhausted_video_and_upstream_still_allow_one_actual_movie_dispute(tmp_path, monkeypatch):
    from tests.test_repair_agent import configured
    from novel_manga.application.repair import agent, managed, history, upstream
    from novel_manga.application.review import final_dispute
    directory, clips, _ = configured(tmp_path)
    media = read_json(directory/'thin_media_report.json')
    movie = directory/'movie.mp4'; movie.write_bytes(b'current delivered movie')
    image = directory/'frame.jpg'; image.write_bytes(b'current delivered JPEG')
    frame = {'clip_id': 'a', 'path': str(image), 'seconds': 2.5, 'label': 'middle'}
    issue = {'id': 'phone', 'clip_ids': ['a'], 'problem': 'phone visible', 'source_basis': 'sole camera',
             'evidence': 'claimed screen', 'frame_paths': [str(image)]}
    final = {'movie': str(movie), 'policy': final_cut.POLICY, 'context': final_cut.context(directory, media, movie),
             'complete': True, 'passed': False, 'issues': [issue], 'frames': [frame], 'errors': []}
    atomic_write_json(directory/'final_cut_review.json', final)
    review = final_cut.apply_feedback(directory, read_json(directory/'episode_review.json'), final)
    atomic_write_json(directory/'episode_review.json', review)
    record = history.load(directory); record['prior_generation_counts'] = {'clips': {'a': 4, 'b': 4}}
    history.save(directory, record)
    for cid in ('a', 'b'):
        upstream.record(directory, cid, {}, {})
        upstream.record(directory, cid, {}, {})
    assert not upstream.available(directory, 'a')
    before_history = (directory/'repair_history/history.json').read_bytes()
    before_state = managed.input_state(directory, clips[0], review['clips']['a'],
                                       managed.current_takes(directory, {'clips': clips}, review))
    # A failure recorded by the previous implementation must not hide the new review allowance.
    before_state.pop('final_dispute_available', None)
    atomic_write_json(directory/'repair_routing.json', {'a': {'status': 'blocked', 'inputs': before_state, 'reason': 'old keep rejected'}})
    eligible, blocked = managed.candidates(directory)
    assert eligible == ['a'] and 'b' in blocked
    calls = []
    monkeypatch.setattr(agent, 'propose', lambda *a, **k: {'action': 'keep', 'reason': 'phone absent', 'evidence': ['frame']})
    def observe(d, current, *, dispute):
        calls.append('independent'); return resolution(current, dispute['findings'], extra=True)
    monkeypatch.setattr(independent, 'observe', observe)
    result = managed.prepare_agent_batch(directory, ['a'])
    assert result['review_changed'] and result['skip_render'] and result['changed'] == []
    assert calls == ['independent']
    assert (directory/'repair_history/history.json').read_bytes() == before_history
    assert not (directory/'repair_budget_grants.json').exists()
    assert managed.candidates(directory)[0] == []  # new watch issue remains, no second arbitration


def test_picture_dispute_cannot_overrule_measured_audio_issue(tmp_path, monkeypatch):
    from novel_manga.application.review import final_dispute
    media, final = current_case(tmp_path, monkeypatch)
    final['issues'] = copy.deepcopy(final['issues'])
    final['issues'][0]['audio_evidence'] = 'actual speech timestamps'
    atomic_write_json(tmp_path/'final_cut_review.json', final)
    assert not final_dispute.available(tmp_path, 'a')
    monkeypatch.setattr(independent, 'observe', lambda *a, **k: pytest.fail('picture review cannot adjudicate audio'))
    with pytest.raises(ValueError, match='picture'):
        final_dispute_review.adjudicate(tmp_path, 'a', {'reason': 'silent', 'evidence': ['JPEG']})


@pytest.mark.parametrize('next_action', ['keep', 'stop'])
def test_exhausted_upstream_candidate_gets_feedback_before_dispute_or_stop(tmp_path, monkeypatch, next_action):
    from tests.test_repair_agent import configured
    from novel_manga.application.repair import agent, managed, history, upstream
    directory, clips, _ = configured(tmp_path)
    movie = directory/'movie.mp4'; movie.write_bytes(b'current movie')
    image = directory/'frame.jpg'; image.write_bytes(b'current movie frame')
    media = read_json(directory/'thin_media_report.json')
    issue = {'id': 'phone', 'clip_ids': ['a'], 'problem': 'phone visible', 'source_basis': 'sole camera',
             'evidence': 'claimed screen', 'frame_paths': [str(image)]}
    final = {'movie': str(movie), 'policy': final_cut.POLICY, 'context': final_cut.context(directory, media, movie),
             'complete': True, 'passed': False, 'issues': [issue], 'errors': [],
             'frames': [{'clip_id': 'a', 'path': str(image), 'seconds': 2.5, 'label': 'middle'}]}
    atomic_write_json(directory/'final_cut_review.json', final)
    atomic_write_json(directory/'episode_review.json', final_cut.apply_feedback(
        directory, read_json(directory/'episode_review.json'), final))
    record = history.load(directory); record['prior_generation_counts'] = {'clips': {'a': 4}}
    history.save(directory, record)
    atomic_write_json(directory/'repair_budget_grants.json', {'a': {'limit': 4, 'reason': 'existing authorized take'}})
    for _ in range(2): upstream.record(directory, 'a', {}, {})
    review = read_json(directory/'episode_review.json')
    old_state = managed.input_state(directory, clips[0], review['clips']['a'],
                                    managed.current_takes(directory, {'clips': clips}, review))
    old_state['repair_evidence_policy'] = 'same-take-frames-v8-active-reference-aliases-and-staged-inputs'
    atomic_write_json(directory/'repair_routing.json', {'a': {
        'status': 'blocked', 'reason': 'upstream repair allowance used', 'inputs': old_state}})
    assert 'a' in managed.candidates(directory)[0]
    names = ('chapter_script.json', 'clip_plan.json', 'repair_history/history.json', 'repair_budget_grants.json')
    before = {name: (directory/name).read_bytes() for name in names}
    candidates = []; observations = []
    def propose(*args, rejections=None):
        candidates.append(rejections or [])
        assert len(candidates) <= 2
        return {'action': 'input' if len(candidates) == 1 else next_action,
                'reason': 'verify current phone evidence', 'evidence': ['current JPEG']}
    monkeypatch.setattr(agent, 'propose', propose)
    monkeypatch.setattr(upstream, 'input_proposal', lambda *a: pytest.fail('exhausted input must not execute'))
    def observe(d, current, *, dispute):
        observations.append('independent')
        return resolution(current, dispute['findings'], extra=True)
    monkeypatch.setattr(independent, 'observe', observe)
    result = managed.prepare_agent_batch(directory, ['a'])
    assert len(candidates) == 2 and candidates[1][0]['stage'] == 'upstream_budget'
    feedback = str(candidates[1][0])
    assert 'video_remaining=0' in feedback and 'keep' in feedback and 'stop' in feedback
    assert result['skip_render'] and result['changed'] == []
    assert observations == (['independent'] if next_action == 'keep' else [])
    assert result['review_changed'] is (next_action == 'keep')
    if next_action == 'stop': assert 'agent stopped' in result['blocked']['a']
    assert read_json(directory/'final_cut_review.json')['passed'] is False
    assert {name: (directory/name).read_bytes() for name in names} == before
    assert history.generation_counts(directory)['a'] == 4


def test_dispute_resolution_survives_cached_recombine_and_controlled_movie_publication(tmp_path, monkeypatch):
    from novel_manga.application.review import final_dispute
    media, final = current_case(tmp_path, monkeypatch)
    original_observe = independent.observe
    monkeypatch.setattr(independent, 'observe', lambda d, f, *, dispute: resolution(f, dispute['findings']))
    final_dispute_review.adjudicate(tmp_path, 'a', {'reason': 'phone absent', 'evidence': ['current JPEG']})
    monkeypatch.setattr(independent, 'observe', original_observe)
    saved = read_json(tmp_path/'final_cut_review.json')
    assert independent.combine(tmp_path, saved)['passed']
    original_record = Path(saved['dispute_reviews'][0]['record'])
    original_bytes = original_record.read_bytes()
    published = tmp_path/'published.mp4'; published.write_bytes(Path(final['movie']).read_bytes())
    saved.update(movie=str(published), context=final_cut.context(tmp_path, media, published))
    independent.relocate(tmp_path, saved)
    recombined = independent.combine(tmp_path, saved)
    assert recombined['passed'] and not recombined['issues']
    assert final_cut.current(tmp_path, media, recombined)
    assert original_record.read_bytes() == original_bytes
