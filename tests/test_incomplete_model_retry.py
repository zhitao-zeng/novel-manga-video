"""Unfinished service calls share existing attempts, time and token allocations."""
import json

import httpx
import pytest

from novel_manga.llm import client, transport
from novel_manga.llm.config import JsonEndpoint


def _sse(content='{"consistent":false}', finish='stop'):
    choice = {'delta': {'content': content}}
    if finish is not None:
        choice['finish_reason'] = finish
    return 'data: ' + json.dumps({'choices': [choice]}) + '\n\ndata: [DONE]\n\n'


def _http(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(client.httpx, 'Client', lambda **kw:
                        real_client(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.parametrize('first', [
    'data: [DONE]\n',
    'data: {"error":{"type":"api_error","code":"upstream_failure","message":"Model service request failed."}}\n',
    'data: {"error":{"type":"upstream_stream_error","code":"upstream_stream_error","message":"Upstream stream was interrupted."}}\n',
    _sse('{"consistent":true}', finish=None),
    _sse('', finish='stop'),
    _sse('unfinished answer'),
])
def test_unfinished_stream_retries_same_request_and_returns_valid_failure(monkeypatch, first):
    calls = []
    clock = [0.0]
    monkeypatch.setattr(client.time, 'monotonic', lambda: clock[0])

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            clock[0] += 4
            return httpx.Response(200, text=first)
        return httpx.Response(200, text=_sse())

    _http(monkeypatch, respond)
    settings = JsonEndpoint('judge', ('http://retry.invalid/v1',), stream=True, min_max_tokens=0)
    answer = client.ask_json([], client.obj({'consistent': {'type': 'boolean'}}),
                             name='review', settings=settings, max_tokens=700, timeout=10)
    assert answer == {'consistent': False}
    assert len(calls) == 2
    assert calls[0].content == calls[1].content
    assert [r.extensions['timeout']['read'] for r in calls] == [10, 6]


@pytest.mark.parametrize('body', [
    {'choices': []},
    {'choices': [{'message': {'content': ''}, 'finish_reason': 'stop'}]},
    {'choices': [{'message': {'content': 'I have not finished'}, 'finish_reason': 'stop'}]},
])
def test_non_stream_empty_or_invalid_answer_is_not_an_empty_success(monkeypatch, body):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, json=body if len(calls) == 1 else {
            'choices': [{'message': {'content': '{"consistent":false}'}, 'finish_reason': 'stop'}]})

    _http(monkeypatch, respond)
    answer = client.ask_json([], {}, name='review', settings=JsonEndpoint('judge', ('http://retry.invalid/v1',)))
    assert answer == {'consistent': False} and len(calls) == 2


@pytest.mark.parametrize('allow_retry, expected', [(True, 3), (False, 1)])
def test_empty_service_stops_at_existing_attempt_limit(monkeypatch, allow_retry, expected):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, text='data: [DONE]\n')

    _http(monkeypatch, respond)
    with pytest.raises(RuntimeError, match='empty streamed response'):
        client.ask_json([], {}, name='review', retry_truncated=allow_retry,
                        settings=JsonEndpoint('judge', ('http://retry.invalid/v1',), stream=True, min_max_tokens=0))
    assert len(calls) == expected


def test_network_read_interruption_uses_existing_retry(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadError('connection lost', request=request)
        return httpx.Response(200, text=_sse())

    _http(monkeypatch, respond)
    assert client.ask_json([], {}, name='review', settings=JsonEndpoint(
        'judge', ('http://retry.invalid/v1',), stream=True, min_max_tokens=0)) == {'consistent': False}
    assert len(calls) == 2


def test_partial_stream_cannot_restart_expired_budget(monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(client.time, 'monotonic', lambda: clock[0])

    def respond(request):
        calls.append(request)
        clock[0] += 10
        return httpx.Response(200, text=_sse('{"consistent":true}', finish=None))

    _http(monkeypatch, respond)
    with pytest.raises(httpx.ReadTimeout, match='stream request budget exhausted'):
        client.ask_json([], {}, name='review', timeout=10, settings=JsonEndpoint(
            'judge', ('http://retry.invalid/v1',), stream=True, min_max_tokens=0))
    assert len(calls) == 1


def test_unfinished_reply_cannot_repeat_a_pooled_reasoning_allocation(monkeypatch):
    calls = []

    def respond(request):
        calls.append(json.loads(request.content)['max_tokens'])
        if len(calls) == 1:
            events = _sse('', 'length').replace('data: [DONE]',
                'data: {"choices":[],"usage":{"completion_tokens_details":{"reasoning_tokens":1600}}}\n\ndata: [DONE]')
            return httpx.Response(200, text=events)
        return httpx.Response(200, text='data: [DONE]\n')

    _http(monkeypatch, respond)
    with pytest.raises(RuntimeError, match='empty streamed response'):
        client.ask_json([], {}, name='review', max_tokens=1600, settings=JsonEndpoint(
            'judge', ('http://retry.invalid/v1',), stream=True, min_max_tokens=0))
    assert calls == [1600, 4800]


def test_non_transient_stream_error_does_not_retry(monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, text='data: {"error":{"code":"insufficient_credits","message":"No credits"}}\n')

    _http(monkeypatch, respond)
    with pytest.raises(RuntimeError, match='insufficient_credits'):
        client.ask_json([], {}, name='review', settings=JsonEndpoint(
            'judge', ('http://retry.invalid/v1',), stream=True, min_max_tokens=0))
    assert len(calls) == 1


def test_empty_stream_preserves_reported_usage_before_retry(monkeypatch):
    recorded = []
    monkeypatch.setattr(transport, '_meter', lambda url, request, body: recorded.append(body.get('usage')))
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, text='data: {"choices":[],"usage":{"total_tokens":123}}\n\ndata: [DONE]\n')
        return httpx.Response(200, text=_sse())

    _http(monkeypatch, respond)
    assert client.ask_json([], {}, name='review', settings=JsonEndpoint(
        'judge', ('http://retry.invalid/v1',), stream=True, min_max_tokens=0)) == {'consistent': False}
    assert recorded == [{'total_tokens': 123}, None]


def test_length_finish_is_not_accepted_even_if_prefix_is_valid_json(monkeypatch):
    calls = []

    def respond(request):
        calls.append(json.loads(request.content)['max_tokens'])
        return httpx.Response(200, text=_sse('{"consistent":true}', 'length') if len(calls) == 1 else _sse())

    _http(monkeypatch, respond)
    assert client.ask_json([], {}, name='review', max_tokens=700, settings=JsonEndpoint(
        'judge', ('http://retry.invalid/v1',), stream=True, min_max_tokens=0)) == {'consistent': False}
    assert calls == [700, 1400]


def test_active_stream_cannot_extend_original_request_deadline(monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(client.time, 'monotonic', lambda: clock[0])

    class ThinkingStream(httpx.SyncByteStream):
        def __iter__(self):
            for seconds in (3, 6, 9, 12):
                clock[0] = seconds
                yield b'data: {"choices":[{"delta":{"reasoning_content":"still thinking"}}]}\n\n'
            yield _sse('{"consistent":true}').encode()

    def respond(request):
        calls.append(request)
        return httpx.Response(200, stream=ThinkingStream())

    _http(monkeypatch, respond)
    with pytest.raises(httpx.ReadTimeout, match='stream request budget exhausted'):
        client.ask_json([], {}, name='review', timeout=10, settings=JsonEndpoint(
            'judge', ('http://retry.invalid/v1',), stream=True, min_max_tokens=0))
    assert len(calls) == 1
