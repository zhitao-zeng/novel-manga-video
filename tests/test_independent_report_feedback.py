from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_manga.application.review import independent, final_cut
from novel_manga.util import atomic_write_json, read_json
from tests.test_independent_final_review import setup


def full_movie(tmp_path, monkeypatch):
    runner, media, final, prior = setup(tmp_path, monkeypatch)
    independent.register(tmp_path, media, prior)
    final['frames'] = []
    for n in range(67):
        image = tmp_path/f'movie-{n:03d}.jpg'; image.write_bytes(b'actual current movie JPEG')
        final['frames'].append({'clip_id': 'a' if n < 3 else f'clip_{min(n//3+1,22):02d}',
            'path': str(image), 'seconds': n, 'label': 'interaction card' if n == 66 else ('start', 'middle', 'end')[n % 3]})
    Path(final['movie']).write_bytes(b'new current delivered movie')
    final['context'] = final_cut.context(tmp_path, media, final['movie'])
    return final, read_json(tmp_path/'independent_review.json')['issues']


def report(packet, *, missing=False):
    primary = packet['primary_frames']; assert len(primary) == 23
    image = primary[-2]['path']
    return {'complete': True, 'passed': False,
        'observations': [{'frame_path': row['path'], 'observation': 'actual observed picture'}
                         for row in (primary[:-1] if missing else primary)],
        'issues': [{'id': 'gold', 'clip_ids': ['clip_22'], 'problem': 'gold seam',
                    'evidence': 'visible seam', 'source_basis': 'plain gray wardrobe', 'frame_paths': [image]}],
        'prior_findings': [{'id': 'phone', 'status': 'resolved', 'evidence': 'phone not visible',
                            'frame_paths': [primary[0]['path']]}]}


@pytest.mark.parametrize('second_complete', [True, False])
def test_missing_interaction_picture_gets_one_precise_same_deadline_report_correction(tmp_path, monkeypatch, second_complete):
    final, old_issues = full_movie(tmp_path, monkeypatch)
    calls = []; clock = [100.0]; monkeypatch.setattr(independent.time, 'monotonic', lambda: clock[0])
    config = {'runs_root': str(tmp_path/'runs')}
    def execute(run, *, config, timeout, prompt=None):
        work = Path(config['runs_root'])/run; packet = read_json(work/'input/review.json')
        calls.append((run, timeout)); clock[0] += 150
        if len(calls) == 1:
            assert timeout == 600 and prompt is None
        else:
            assert len(calls) == 2 and calls[1][0] == calls[0][0] and timeout == 450
            assert '--continue' in config['extra_args']
            assert 'input/frame-066.jpg' in prompt.read_text()
            assert 'Preserve' in prompt.read_text()
        atomic_write_json(work/'output/review.json', report(packet, missing=len(calls) == 1 or not second_complete))
        return SimpleNamespace(ok=True, produced=['review.json'], directory=work/f'attempts/{len(calls)}')
    monkeypatch.setattr(independent, 'run_agent', execute)
    result = independent.observe(tmp_path, final, config=config)
    assert len(calls) == 2 and len(result['candidates']) == 2
    candidates = result['candidates']
    assert candidates[0]['status'] == 'rejected' and 'input/frame-066.jpg' in candidates[0]['error']
    first = read_json(Path(candidates[0]['report'])); second = read_json(Path(candidates[1]['report']))
    assert len(first['observations']) == 22
    assert len(second['observations']) == (23 if second_complete else 22)
    assert first['issues'] == second['issues']  # real visual findings retained across protocol correction
    if second_complete:
        assert result['complete'] and not result['passed'] and not result['errors']
        assert [i['id'] for i in result['issues']] == ['gold']
    else:
        assert not result['complete'] and not result['passed']
        assert 'input/frame-066.jpg' in result['errors'][0] and result['issues'] == old_issues
    assert not (tmp_path/'repair_budget_grants.json').exists()


@pytest.mark.parametrize('failure', ['service', 'deadline'])
def test_service_failure_or_spent_deadline_never_starts_report_retry(tmp_path, monkeypatch, failure):
    final, old_issues = full_movie(tmp_path, monkeypatch)
    calls = []; clock = [100.0]; monkeypatch.setattr(independent.time, 'monotonic', lambda: clock[0])
    config = {'runs_root': str(tmp_path/'runs')}
    def execute(run, *, config, timeout):
        calls.append(run); assert len(calls) == 1
        work = Path(config['runs_root'])/run
        atomic_write_json(work/'output/review.json', report(read_json(work/'input/review.json'), missing=True))
        if failure == 'deadline': clock[0] += 601
        return SimpleNamespace(ok=failure != 'service', produced=['review.json'], directory=work/'attempts/one')
    monkeypatch.setattr(independent, 'run_agent', execute)
    result = independent.observe(tmp_path, final, config=config)
    assert len(calls) == 1 and not result['complete'] and result['issues'] == old_issues


@pytest.mark.parametrize('failure', ['wrong_path', 'prior_disposition'])
def test_report_feedback_identifies_wrong_path_or_missing_prior_id(tmp_path, monkeypatch, failure):
    final, _ = full_movie(tmp_path, monkeypatch)
    config = {'runs_root': str(tmp_path/'runs')}; calls = []
    def execute(run, *, config, timeout, prompt=None):
        calls.append(run); work = Path(config['runs_root'])/run
        answer = report(read_json(work/'input/review.json'))
        if len(calls) == 1:
            if failure == 'wrong_path': answer['issues'][0]['frame_paths'] = ['input/not-a-supplied-frame.jpg']
            else: answer['prior_findings'] = []
        else:
            assert len(calls) == 2
            assert ('input/not-a-supplied-frame.jpg' if failure == 'wrong_path' else 'phone') in prompt.read_text()
        atomic_write_json(work/'output/review.json', answer)
        return SimpleNamespace(ok=True, produced=['review.json'], directory=work/f'attempts/{len(calls)}')
    monkeypatch.setattr(independent, 'run_agent', execute)
    result = independent.observe(tmp_path, final, config=config)
    assert len(calls) == 2 and result['complete'] and not result['errors']
