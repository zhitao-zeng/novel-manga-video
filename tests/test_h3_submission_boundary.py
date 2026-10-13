"""A lost H3 reply must not create another task, including after restart."""
import json

import httpx
import pytest

from novel_manga.providers.phanrouter_tasks import SubmissionUncertain
from novel_manga.media.policy import resubmittable
from novel_manga.util import atomic_write_json
from support.h3_provider import make_provider


@pytest.mark.parametrize('pool', [False, True])
@pytest.mark.parametrize('failure', ['read_timeout', 'write_timeout', '502', '503', 'invalid_json', 'missing_id'])
def test_unconfirmed_post_is_not_repeated_after_restart_or_changed_input(tmp_path, monkeypatch, pool, failure):
    posts = []
    def handler(request):
        if request.method == 'POST':
            posts.append(json.loads(request.content))
            if len(posts) == 1:
                if failure == 'read_timeout':
                    raise httpx.ReadTimeout('accepted but reply lost', request=request)
                if failure == 'write_timeout':
                    raise httpx.WriteTimeout('acceptance unknown', request=request)
                if failure.isdigit():
                    return httpx.Response(int(failure))
                if failure == 'invalid_json':
                    return httpx.Response(200, content=b'not JSON')
                return httpx.Response(200, json={})
            return httpx.Response(200, json={'id': 'duplicate'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'mocked video')
        return httpx.Response(200, json={'status': 'completed'})

    def provider():
        p, card = make_provider(tmp_path, handler, base_url='pool' if pool else 'http://h3.invalid',
                                resident=[{'name': 'one', 'url': 'http://h3.invalid'}],
                                unreachable_cooldown_minutes=0)
        if pool:
            monkeypatch.setattr(p.pool, 'problem', lambda _: None)
        return p, card
    output = tmp_path/'clip.mp4'
    p, card = provider()
    with pytest.raises(SubmissionUncertain) as caught:
        p.create_video('fixed input', None, output, 5, additional_images=(card,))
    assert not resubmittable(caught.value)
    receipt = output.with_suffix('.mp4.task.json')
    saved = receipt.read_bytes()
    assert json.loads(saved)['submit_uncertain_at'] and len(posts) == 1
    p.client.close()
    # A paid-API override must not release a local H3 task whose outcome is unknown.
    monkeypatch.setenv('NOVEL_RESUBMIT_UNCONFIRMED', '9999999999')
    for prompt in ('fixed input', 'changed input'):
        p, card = provider()
        with pytest.raises(SubmissionUncertain):
            p.create_video(prompt, None, output, 5, additional_images=(card,))
        assert receipt.read_bytes() == saved and len(posts) == 1
        p.client.close()


def test_process_interruption_keeps_submission_record_before_post(tmp_path):
    output = tmp_path/'clip.mp4'
    posts = []
    def handler(request):
        posts.append(request.method)
        raise SystemExit('simulated process interruption after accepting POST')
    provider, card = make_provider(tmp_path, handler, base_url='http://h3.invalid')
    with pytest.raises(SystemExit):
        provider.create_video('scene', None, output, 5, additional_images=(card,))
    record = json.loads(output.with_suffix('.mp4.task.json').read_text())
    assert record['submit_uncertain_at'] and not record.get('task_id')
    with pytest.raises(SubmissionUncertain):
        provider.create_video('scene', None, output, 5, additional_images=(card,))
    assert posts == ['POST']
    provider.client.close()


@pytest.mark.parametrize('status', [502, 503, 404])
def test_poll_failure_keeps_original_task_and_resume_downloads_without_post(tmp_path, monkeypatch, status):
    posts = []; ready = False
    def handler(request):
        if request.method == 'POST':
            posts.append(1)
            return httpx.Response(200, json={'id': 'original'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'mocked video')
        return httpx.Response(200, json={'status': 'completed'}) if ready else httpx.Response(status)
    provider, card = make_provider(tmp_path, handler, resident=[{'name': 'one', 'url': 'http://h3.invalid'}],
                                   unreachable_cooldown_minutes=0)
    monkeypatch.setattr(provider.pool, 'problem', lambda _: None)
    provider.settings.poll_timeout = .03
    output = tmp_path/'clip.mp4'
    with pytest.raises(SubmissionUncertain):
        provider.create_video('scene', None, output, 5, additional_images=(card,))
    assert json.loads(output.with_suffix('.mp4.task.json').read_text())['task_id'] == 'original'
    ready = True
    provider.settings.poll_timeout = 2
    provider.create_video('scene', None, output, 5, additional_images=(card,))
    assert posts == [1] and output.read_bytes() == b'mocked video'
    provider.client.close()


@pytest.mark.parametrize('record', [{'submit_uncertain_at': 1}, {'task_id': 'still-pending'}])
def test_host_cannot_change_attempt_to_escape_an_unresolved_submission(tmp_path, monkeypatch, record):
    from support.h3_admission import runner_for, _bound
    from novel_manga.application.profiles import h3_stamp
    from novel_manga.application.repair import history
    from novel_manga.application.preparation import request_check
    clip = _bound(); clip['prompt_h3_of'] = h3_stamp(clip)
    runner = runner_for(tmp_path, monkeypatch, clip)
    receipt = runner.context.work/'clips/clip_01/attempt_01/clip.mp4.task.json'
    atomic_write_json(receipt, record)
    original = receipt.read_bytes()
    monkeypatch.setattr(request_check, 'before_generation', lambda *a: pytest.fail('no new preflight'))
    with pytest.raises(SubmissionUncertain):
        runner.generate_clip(clip, 2)
    assert receipt.read_bytes() == original
    assert history.generation_counts(runner.context.episode_dir) == {}


def test_connection_refused_before_acceptance_can_try_a_reachable_instance(tmp_path, monkeypatch):
    posts = []; accepted = []
    def handler(request):
        if request.method == 'POST':
            posts.append(request.url.host)
            if request.url.host == '10.0.0.1':
                raise httpx.ConnectError('connection refused before sending', request=request)
            accepted.append('only-task')
            return httpx.Response(200, json={'id': 'only-task'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'complete')
        return httpx.Response(200, json={'status': 'completed'})
    provider, card = make_provider(tmp_path, handler)
    from support.h3_provider import first_listed_first
    first_listed_first(monkeypatch)
    monkeypatch.setattr(provider.pool, 'problem', lambda _: None)
    output = tmp_path/'clip.mp4'
    provider.create_video('scene', None, output, 5, additional_images=(card,))
    assert posts == ['10.0.0.1', '10.0.0.2'] and accepted == ['only-task']
    assert not json.loads(output.with_suffix('.mp4.task.json').read_text()).get('submit_uncertain_at')
    provider.client.close()


@pytest.mark.parametrize('status', [400, 429])
def test_explicit_rejection_leaves_no_unconfirmed_hold(tmp_path, monkeypatch, status):
    posts = []
    def handler(request):
        if request.method == 'POST':
            posts.append(1)
            return httpx.Response(status) if len(posts) == 1 else httpx.Response(200, json={'id': 'only-task'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'complete')
        return httpx.Response(200, json={'status': 'completed'})
    provider, card = make_provider(tmp_path, handler)
    monkeypatch.setattr(provider.pool, 'problem', lambda _: None)
    output = tmp_path/'clip.mp4'
    with pytest.raises(RuntimeError) as caught:
        provider.create_video('scene', None, output, 5, additional_images=(card,))
    assert not isinstance(caught.value, SubmissionUncertain)
    record = json.loads(output.with_suffix('.mp4.task.json').read_text())
    assert record['submit_not_accepted_at'] and not record.get('submit_uncertain_at')
    provider.create_video('scene', None, output, 5, additional_images=(card,))
    assert len(posts) == 2 and output.read_bytes() == b'complete'
    provider.client.close()


@pytest.mark.parametrize('lost_at', ['submit', 'poll'])
def test_host_and_provider_share_one_submission_across_reentry(tmp_path, monkeypatch, lost_at):
    from support.h3_admission import runner_for, _bound
    from novel_manga.application.profiles import h3_stamp
    from novel_manga.application.repair import history
    from novel_manga.application.preparation import request_check
    from novel_manga.application.rendering import submission
    from novel_manga.media import generation

    clip = _bound(); clip['prompt_h3_of'] = h3_stamp(clip)
    runner = runner_for(tmp_path, monkeypatch, clip); ctx = runner.context
    posts = []; ready = False; checks = []
    def handler(request):
        if request.method == 'POST':
            posts.append(json.loads(request.content))
            if lost_at == 'submit':
                raise httpx.ReadTimeout('accepted but reply lost', request=request)
            return httpx.Response(200, json={'id': 'original'})
        if not ready:
            return httpx.Response(502)
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'mocked video')
        return httpx.Response(200, json={'status': 'completed'})
    provider, _ = make_provider(tmp_path, handler, base_url='http://h3.invalid')
    ctx.provider = provider
    monkeypatch.setattr(request_check, 'ask_json', lambda *a, **kw:
                        checks.append(1) or {'observations': ['matching references'], 'findings': []})
    monkeypatch.setattr(submission, 'wait_for_inflight_redraws', lambda *a: None)
    monkeypatch.setattr(submission, 'acquire_inflight_slot', lambda *a: None)
    monkeypatch.setattr(submission, 'media_duration', lambda *a: 15)
    grants = ctx.episode_dir/'repair_budget_grants.json'
    atomic_write_json(grants, {'*': {'limit': 1, 'reason': 'original single take'}})
    original_grants = grants.read_bytes()
    history.begin_trial(ctx.episode_dir, {'clip_01'}, 'test', after_plan=ctx.clip_plan, after_notes={})
    record = history.load(ctx.episode_dir); record['trials'][-1]['managed'] = True
    history.save(ctx.episode_dir, record)
    with pytest.raises(SubmissionUncertain):
        runner.generate_clip(clip, 1)
    output = ctx.work/'clips/clip_01/attempt_01/clip.mp4'
    request_path = output.parent/'request.json'
    original_request = request_path.read_bytes()
    assert len(posts) == 1 and history.generation_counts(ctx.episode_dir).get('clip_01', 0) == 0
    if lost_at == 'submit':
        for attempt in (1, 2):
            with pytest.raises(SubmissionUncertain):
                runner.generate_clip(clip, attempt)
        assert request_path.read_bytes() == original_request
    else:
        ready = True
        assert runner.generate_clip(clip, 1) == output
        assert history.generation_counts(ctx.episode_dir)['clip_01'] == 1
        assert runner.generate_clip(clip, 1) == output
        assert history.generation_counts(ctx.episode_dir)['clip_01'] == 1
    assert len(posts) == len(checks) == 1
    assert grants.read_bytes() == original_grants
    provider.client.close()


def test_host_preserves_original_request_when_pending_inputs_change(tmp_path, monkeypatch):
    from support.h3_admission import runner_for, _bound
    from novel_manga.application.profiles import h3_stamp
    from novel_manga.media import generation
    from novel_manga.application.preparation import request_check
    clip = _bound(); clip['prompt_h3_of'] = h3_stamp(clip)
    runner = runner_for(tmp_path, monkeypatch, clip); ctx = runner.context
    directory = ctx.work/'clips/clip_01/attempt_01'
    original = generation.build_request(ctx, clip, 1)[0]
    atomic_write_json(directory/'request.json', original)
    atomic_write_json(directory/'clip.mp4.task.json', {'task_id': 'original'})
    clip['repair_take'] = 1
    monkeypatch.setattr(request_check, 'before_generation', lambda *a: pytest.fail('must preserve pending input'))
    with pytest.raises(SubmissionUncertain):
        runner.generate_clip(clip, 1)
    assert json.loads((directory/'request.json').read_text()) == original


def test_old_output_does_not_prove_the_replacement_task_completed(tmp_path):
    posts = []
    def handler(request):
        if request.method == 'POST':
            posts.append(1)
            return httpx.Response(200, json={'id': f't{len(posts)}'})
        if request.url.path.endswith('/content'):
            return httpx.Response(200, content=b'old completed video')
        if request.url.path.endswith('/t2'):
            return httpx.Response(502)
        return httpx.Response(200, json={'status': 'completed'})
    provider, card = make_provider(tmp_path, handler, base_url='http://h3.invalid')
    output = tmp_path/'clip.mp4'
    provider.create_video('original scene', None, output, 5, additional_images=(card,))
    with pytest.raises(SubmissionUncertain):
        provider.create_video('replacement scene', None, output, 5, additional_images=(card,))
    saved = output.with_suffix('.mp4.task.json').read_bytes()
    with pytest.raises(SubmissionUncertain):
        provider.create_video('third scene', None, output, 5, additional_images=(card,))
    assert len(posts) == 2 and output.with_suffix('.mp4.task.json').read_bytes() == saved
    provider.client.close()
