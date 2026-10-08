"""Shared endpoint limits across real processes, without model or network calls."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest

from novel_manga.llm import resources, transport
from novel_manga.llm.config import JsonEndpoint


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', json.dumps([{'base': 'http://model.example:8038/v1', 'slots': 2}]))
    monkeypatch.setenv('NOVEL_MODEL_INFLIGHT_DIR', str(tmp_path / 'model-slots'))
    transport._UNREACHABLE.clear()
    yield tmp_path / 'model-slots'
    transport._UNREACHABLE.clear()


CHILD = """
import os, sys, time
from pathlib import Path
from novel_manga.llm.resources import model_slot
pause = os.environ.get('MODEL_TEST_PAUSE_FILE')
try:
    with model_slot(sys.argv[1], deadline=time.monotonic()+float(sys.argv[2]),
                    on_wait=lambda: print('WAITING', flush=True),
                    cancelled=(lambda: Path(pause).exists()) if pause else None):
        print('ACQUIRED', flush=True)
        sys.stdin.readline()
except TimeoutError:
    print('TIMED_OUT', flush=True)
except InterruptedError:
    print('CANCELLED', flush=True)
"""


@contextmanager
def child(base='http://model.example:8038', seconds=5):
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    process = subprocess.Popen([sys.executable, '-c', CHILD, base, str(seconds)], env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        yield process
    finally:
        if process.poll() is None:
            try:
                process.communicate('release\n', timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


def release(process):
    process.stdin.write('release\n')
    process.stdin.flush()
    assert process.wait(timeout=3) == 0


def test_two_process_slots_are_shared_across_url_path_aliases(configured):
    with child('http://MODEL.example:8038/v1/') as first, child() as second, child('http://unused.example') as unused:
        assert first.stdout.readline().strip() == 'ACQUIRED'
        assert second.stdout.readline().strip() == 'ACQUIRED'
        # Unconfigured callers retain ordinary behavior and create no extra pool.
        assert unused.stdout.readline().strip() == 'ACQUIRED'
        with child('http://model.example:8038/v1/messages') as third:
            assert third.stdout.readline().strip() == 'WAITING'
            release(first)
            assert third.stdout.readline().strip() == 'ACQUIRED'
    assert [p.name for p in configured.iterdir()] == ['model.example_8038']


def test_wait_deadline_and_callback_use_real_held_locks(configured, monkeypatch):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', '[{"base":"http://model.example:8038","slots":1}]')
    with resources.model_slot('http://model.example:8038/v1'):
        with child(seconds=0.1) as waiting:
            assert waiting.stdout.readline().strip() == 'WAITING'
            assert waiting.stdout.readline().strip() == 'TIMED_OUT'
            assert waiting.wait(timeout=3) == 0
    with resources.model_slot('http://model.example:8038', deadline=time.monotonic() + 1):
        pass


def test_pause_after_first_wait_interrupts_child_without_starting_work(configured, tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', '[{"base":"http://model.example:8038","slots":1}]')
    pause = tmp_path / 'paused'
    monkeypatch.setenv('MODEL_TEST_PAUSE_FILE', str(pause))
    with resources.model_slot('http://model.example:8038'):
        with child(seconds=5) as waiting:
            assert waiting.stdout.readline().strip() == 'WAITING'
            pause.touch()
            assert waiting.stdout.readline().strip() == 'CANCELLED'
            assert waiting.wait(timeout=3) == 0
    with resources.model_slot('http://model.example:8038', deadline=time.monotonic() + 1):
        pass


def test_already_paused_request_does_not_acquire_free_slot(configured):
    with pytest.raises(InterruptedError):
        with resources.model_slot('http://model.example:8038', cancelled=lambda: True):
            pytest.fail('paused request acquired a slot')
    assert not list(configured.rglob('slot_*.lock'))


def test_agent_reservation_leaves_shared_capacity_for_host_checks(configured):
    base = 'http://model.example:8038'
    with resources.model_slot(base, reserve=1):
        with pytest.raises(TimeoutError):
            with resources.model_slot(base, reserve=1, deadline=time.monotonic()+0.02):
                pytest.fail('second Agent used the reserved host slot')
        with child(base) as host:
            assert host.stdout.readline().strip() == 'ACQUIRED'


def test_reservation_keeps_single_slot_endpoints_usable(configured, monkeypatch):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', '[{"base":"http://model.example:8038","slots":1}]')
    with resources.model_slot('http://model.example:8038', reserve=2, deadline=time.monotonic()+0.1):
        pass


def test_configured_pools_for_different_endpoints_are_independent(configured, monkeypatch):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', json.dumps([
        {'base': 'http://model.example:8038', 'slots': 1},
        {'base': 'http://other.example:8038', 'slots': 1},
    ]))
    with resources.model_slot('http://model.example:8038'):
        with child('http://other.example:8038') as other:
            assert other.stdout.readline().strip() == 'ACQUIRED'


def test_comma_endpoints_share_one_group_capacity(configured, monkeypatch):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', json.dumps([
        {'base': 'http://model.example:8038/v1,http://other.example:8039/v1', 'slots': 1},
    ]))
    with resources.model_slot('http://model.example:8038'):
        with child('http://other.example:8039/v1', seconds=0.1) as waiting:
            assert waiting.stdout.readline().strip() == 'WAITING'
            assert waiting.stdout.readline().strip() == 'TIMED_OUT'
    assert [p.name for p in configured.iterdir()] == ['model.example_8038']


def test_overlapping_groups_and_resource_aliases_use_minimum_capacity(configured, monkeypatch):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', json.dumps([
        {'base': 'http://model.example:8038/v1,http://other.example:8039/v1', 'slots': 3},
        {'base': 'http://other.example:8039,http://third.example:8040', 'slots': 2},
        {'base': 'http://model.example:8038', 'slots': 1},
    ]))
    with resources.model_slot('http://third.example:8040'):
        with child('http://model.example:8038/v1', seconds=0.1) as waiting:
            assert waiting.stdout.readline().strip() == 'WAITING'
            assert waiting.stdout.readline().strip() == 'TIMED_OUT'
    assert [p.name for p in configured.iterdir()] == ['model.example_8038']


def test_no_configuration_or_unmatched_endpoint_has_no_files(tmp_path, monkeypatch):
    monkeypatch.delenv('NOVEL_MODEL_RESOURCES', raising=False)
    monkeypatch.setenv('NOVEL_MODEL_INFLIGHT_DIR', str(tmp_path / 'slots'))
    with resources.model_slot('http://model.example:8038', deadline=0,
                              on_wait=lambda: pytest.fail('unconfigured wait')):
        pass
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', '[{"base":"http://other.example","slots":1}]')
    with resources.model_slot('http://model.example:8038'):
        pass
    assert not (tmp_path / 'slots').exists()


def test_failure_releases_owned_slot(configured, monkeypatch):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', '[{"base":"http://model.example:8038","slots":1}]')
    with pytest.raises(RuntimeError, match='draft failed'):
        with resources.model_slot('http://model.example:8038'):
            raise RuntimeError('draft failed')
    with child() as next_call:
        assert next_call.stdout.readline().strip() == 'ACQUIRED'


def test_shared_limit_file_is_respected_without_rewriting_it(configured):
    directory = configured / 'model.example_8038'
    directory.mkdir(parents=True)
    (directory / 'limit').write_text('1')
    with resources.model_slot('http://model.example:8038'):
        with child(seconds=0.1) as waiting:
            assert waiting.stdout.readline().strip() == 'WAITING'
            assert waiting.stdout.readline().strip() == 'TIMED_OUT'
    assert (directory / 'limit').read_text() == '1'


def test_implicit_default_port_aliases_share_one_pool(configured, monkeypatch):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', '[{"base":"https://Model.example/v1","slots":1}]')
    with resources.model_slot('https://model.example:443'):
        with child('https://model.example/v1/', seconds=0.1) as waiting:
            assert waiting.stdout.readline().strip() == 'WAITING'
            assert waiting.stdout.readline().strip() == 'TIMED_OUT'
    assert (configured / 'model.example_443/slot_00.lock').is_file()


def test_transport_releases_failed_endpoint_before_failover(configured, monkeypatch):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', json.dumps([
        {'base': 'http://model.example:8038', 'slots': 1},
        {'base': 'http://other.example:8038', 'slots': 1},
    ]))
    payload = {'model': 'chosen-model', 'messages': [{'role': 'user', 'content': 'test'}]}
    calls = []

    def respond(request):
        calls.append(request)
        base = f'{request.url.scheme}://{request.url.host}:{request.url.port}'
        with pytest.raises(TimeoutError):
            with resources.model_slot(base, deadline=time.monotonic() + 0.01):
                pass
        if request.url.host == 'model.example':
            raise httpx.ConnectError('unreachable', request=request)
        with resources.model_slot('http://model.example:8038', deadline=time.monotonic() + 0.2):
            pass
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    settings = JsonEndpoint(model='chosen-model', endpoints=(), stream=False)
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as http:
        result = transport.post_any(http, ['http://model.example:8038/v1', 'http://other.example:8038/v1'],
                                    {'Authorization': 'test'}, payload, settings=settings,
                                    deadline=time.monotonic() + 2)
    assert result['choices'][0]['message']['content'] == 'ok'
    assert len(calls) == 2 and all(json.loads(call.content) == payload for call in calls)
    with resources.model_slot('http://other.example:8038', deadline=time.monotonic() + 1):
        pass


def test_transport_waits_past_execution_budget_without_posting_until_admitted(configured, monkeypatch):
    import threading
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', '[{"base":"http://model.example:8038","slots":1}]')
    calls=[];answers=[]
    with httpx.Client(transport=httpx.MockTransport(
            lambda request:calls.append(request) or httpx.Response(200,json={'ok':True}))) as http:
        def request():
            answers.append(transport.post_any(http,['http://model.example:8038/v1'],{}, {},
                settings=JsonEndpoint('model',(),stream=False),deadline=time.monotonic()+.05))
        with resources.model_slot('http://model.example:8038'):
            worker=threading.Thread(target=request);worker.start()
            time.sleep(.15)
            assert worker.is_alive() and not calls
        worker.join(timeout=2)
        assert answers==[{'ok':True}]
    assert transport._UNREACHABLE == {}


@pytest.mark.parametrize('status', [429, 500])
def test_http_error_also_releases_slot_without_changing_retry_policy(configured, monkeypatch, status):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', '[{"base":"http://model.example:8038","slots":1}]')
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, text='existing error')

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            transport.post_any(http, ['http://model.example:8038/v1'], {}, {},
                               settings=JsonEndpoint('model', (), stream=False))
    assert len(calls) == 1
    with child() as after_error:
        assert after_error.stdout.readline().strip() == 'ACQUIRED'


def test_streaming_holds_slot_until_body_consumed_and_releases(configured, monkeypatch):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', '[{"base":"http://model.example:8038","slots":1}]')

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            with pytest.raises(TimeoutError):
                with resources.model_slot('http://model.example:8038', deadline=time.monotonic() + 0.01):
                    pass
            yield b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Body()))) as http:
        result = transport.post_any(http, ['http://model.example:8038/v1'], {}, {'max_tokens': 1},
                                    settings=JsonEndpoint('model', (), stream=True, min_max_tokens=0))
    assert result['choices'][0]['message']['content'] == 'ok'
    with child() as after_stream:
        assert after_stream.stdout.readline().strip() == 'ACQUIRED'
