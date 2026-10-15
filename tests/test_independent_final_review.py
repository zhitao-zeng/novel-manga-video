import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.review import final_cut, independent
from novel_manga.application.rendering import reviewed
from novel_manga.util import atomic_write_json, read_json
from tests.test_final_cut_loop import fixture, verdict


def setup(tmp_path, monkeypatch):
    runner, media, movie = fixture(tmp_path, monkeypatch)
    image = tmp_path/'frame.jpg'; image.write_bytes(b'actual reviewed movie frame')
    frame = {'clip_id': 'a', 'path': str(image), 'seconds': 2.5, 'label': 'middle', 'shot_indexes': [1]}
    final = {**verdict(movie, True), 'policy': final_cut.POLICY,
             'context': final_cut.context(tmp_path, media, movie), 'frames': [frame]}
    atomic_write_json(tmp_path/'final_cut_review.json', final)
    report = {'movie': str(movie), 'complete': True, 'passed': False,
              'observations': ['recording phone visible in the picture'], 'frames': [frame],
              'issues': [{'id': 'phone', 'clip_ids': ['a'], 'problem': 'recording phone visible',
                          'evidence': 'the entire recording phone is shown', 'source_basis': 'only this phone records',
                          'frame_paths': [str(image)]}]}
    return runner, media, final, report


def test_independent_failure_overrides_qwen_pass_and_exports_real_frames(tmp_path, monkeypatch):
    runner, media, final, raw = setup(tmp_path, monkeypatch)
    independent.register(tmp_path, media, raw)
    combined = independent.combine(tmp_path, final)
    assert combined['complete'] and not combined['passed']
    assert combined['automatic_review']['passed'] is True
    assert combined['issues'][0]['id'] == 'phone'
    review = final_cut.apply_feedback(tmp_path, read_json(tmp_path/'episode_review.json'), combined)
    assert 'recording phone visible' in review['feedback']['a']
    from novel_manga.application.repair.agent import export_final_cut
    inputs = tmp_path/'sandbox/input'; inputs.mkdir(parents=True)
    evidence = export_final_cut(tmp_path, inputs, {'clip_id': 'a'}, review['clips']['a'])
    assert not evidence['missing_frames']
    assert (inputs.parent/evidence['frames'][0]['path']).read_bytes() == b'actual reviewed movie frame'


def test_independent_failure_actually_enters_repair_and_new_movie_is_rechecked(tmp_path, monkeypatch):
    runner, media, final, raw = setup(tmp_path, monkeypatch); events = []
    independent.register(tmp_path, media, raw)
    monkeypatch.setattr(reviewed.managed, 'candidates', lambda d, r: (list(r['feedback']), {}))
    def prepare(d, ids):
        assert ids == ['a']; events.append('repair'); return {'changed': ids, 'skip_render': False}
    monkeypatch.setattr(reviewed.managed, 'prepare', prepare)
    def render():
        events.append('render'); Path(final['movie']).write_bytes(b'new repaired movie'); return media
    runner.run = render
    original_observe = independent.observe
    def observe(directory, current):
        if current['context'] == final['context']:
            return original_observe(directory, current)
        events.append('independent_recheck')
        result = {**read_json(tmp_path/'independent_review.json'), 'context': current['context'],
                  'passed': True, 'issues': []}
        atomic_write_json(tmp_path/'independent_review.json', result)
        return result
    monkeypatch.setattr(independent, 'observe', observe)
    def qwen_pass(report):
        current = {**final, 'context': final_cut.context(tmp_path, report, final['movie'])}
        return independent.combine(tmp_path, current)
    result = reviewed.run(runner, repair_existing=True, final_review=qwen_pass)
    assert events == ['repair', 'render', 'independent_recheck']
    assert result['quality_review']['passed'] is True


def test_plain_qwen_pass_cannot_bypass_registered_independent_failure(tmp_path, monkeypatch):
    runner, media, final, raw = setup(tmp_path, monkeypatch)
    independent.register(tmp_path, media, raw)
    assert not final_cut.current(tmp_path, media, final)
    assert final_cut.current(tmp_path, media, independent.combine(tmp_path, final))
    combined = independent.combine(tmp_path, final)
    Path(final['movie']).write_bytes(b'another version')
    assert not final_cut.current(tmp_path, media, combined)


def test_missing_or_misattributed_external_evidence_is_rejected(tmp_path, monkeypatch):
    runner, media, final, raw = setup(tmp_path, monkeypatch)
    bad = copy.deepcopy(raw); bad['issues'][0]['clip_ids'] = ['unknown']
    with pytest.raises(ValueError, match='lacks actual frames'):
        independent.register(tmp_path, media, bad)
    assert not independent.required(tmp_path)
    Path(raw['frames'][0]['path']).unlink()
    with pytest.raises(ValueError, match='lacks actual frames'):
        independent.register(tmp_path, media, raw)


def test_current_movie_review_must_explicitly_resolve_every_prior_finding(tmp_path, monkeypatch):
    runner, media, final, raw = setup(tmp_path, monkeypatch)
    answer = {'complete': True, 'passed': True, 'observations': ['current phone hidden'], 'issues': []}
    with pytest.raises(ValueError, match='omitted a prior finding'):
        independent.validate(answer, final['frames'], previous=raw['issues'])
    answer['prior_findings'] = [{'id': 'phone', 'status': 'resolved', 'evidence': 'current frame hides phone',
                                 'frame_paths': [raw['frames'][0]['path']]}]
    independent.validate(answer, final['frames'], previous=raw['issues'])


def test_independent_failure_respects_exhausted_original_video_limit(tmp_path, monkeypatch):
    runner, media, final, raw = setup(tmp_path, monkeypatch)
    independent.register(tmp_path, media, raw)
    monkeypatch.setattr(reviewed.managed, 'candidates', lambda *a: ([], {'a': 'effective clip retry budget used'}))
    monkeypatch.setattr(reviewed.managed, 'prepare', lambda *a: pytest.fail('must not grant new takes'))
    result = reviewed.run(runner, repair_existing=True, final_review=lambda r: independent.combine(tmp_path, final))
    assert not result['quality_review']['passed']
    assert result['quality_review']['remaining'] == ['a']
    assert result['quality_review']['blocked']['a'] == 'effective clip retry budget used'


def test_failed_independent_service_blocks_acceptance_without_blind_retake(tmp_path, monkeypatch):
    runner, media, final, raw = setup(tmp_path, monkeypatch)
    independent.register(tmp_path, media, raw)
    def unavailable(*args, **kwargs):
        return {'complete': False, 'passed': False, 'issues': [], 'frames': [], 'errors': ['review service failed']}
    monkeypatch.setattr(independent, 'observe', unavailable)
    combined = independent.combine(tmp_path, final)
    assert not combined['complete'] and not combined['passed']
    review = final_cut.apply_feedback(tmp_path, read_json(tmp_path/'episode_review.json'), combined)
    assert review['feedback'] == {}


def test_sandbox_reviewer_gets_current_jpegs_and_must_resolve_old_findings(tmp_path, monkeypatch):
    runner, media, final, raw = setup(tmp_path, monkeypatch)
    independent.register(tmp_path, media, raw)
    Path(final['movie']).write_bytes(b'new actual movie')
    final['context'] = final_cut.context(tmp_path, media, final['movie'])
    config = {'runs_root': str(tmp_path/'runs'), 'extra_args': [], 'repair_extra_body': {}}
    def execute(run, *, config, timeout):
        assert timeout == 600 and config['max_thinking_tokens'] == 0
        assert config['extra_body']['tool_choice']['disable_parallel_tool_use'] is True
        assert 'Read,Write' in config['extra_args']
        work = Path(config['runs_root'])/run
        packet = read_json(work/'input/review.json')
        assert (work/packet['primary_frames'][0]['path']).read_bytes() == b'actual reviewed movie frame'
        assert 'frame_paths' not in packet['prior_findings'][0]
        frame = packet['primary_frames'][0]['path']
        atomic_write_json(work/'output/review.json', {'complete': True, 'passed': True,
            'observations': [{'frame_path': frame, 'observation': 'current sole camera view hides phone'}],
            'issues': [], 'prior_findings': [{'id': 'phone', 'status': 'resolved',
                'evidence': 'current frame has no visible recording device', 'frame_paths': [frame]}]})
        return SimpleNamespace(ok=True, produced=['review.json'], directory=work/'attempts/one')
    monkeypatch.setattr(independent, 'run_agent', execute)
    result = independent.observe(tmp_path, final, config=config)
    assert result['complete'] and result['passed'] and not result['errors']
    assert result['prior_findings'][0]['frame_paths'] == [raw['frames'][0]['path']]
    combined = independent.combine(tmp_path, final)
    assert combined['passed'] and final_cut.current(tmp_path, media, combined)


def test_current_independent_results_survive_controlled_publication_copy(tmp_path, monkeypatch):
    runner, media, final, raw = setup(tmp_path, monkeypatch)
    independent.register(tmp_path, media, raw)
    combined = independent.combine(tmp_path, final)
    published = tmp_path/'published.mp4'; published.write_bytes(Path(final['movie']).read_bytes())
    combined['movie'] = str(published)
    combined['context'] = final_cut.context(tmp_path, media, published)
    independent.relocate(tmp_path, combined)
    assert final_cut.current(tmp_path, media, combined)
