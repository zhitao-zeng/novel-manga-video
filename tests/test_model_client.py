"""Transport regressions shared by planning, source identity and review."""
import copy
import json
import httpx
import pytest
from novel_manga import model_client as client


def test_stream_keeps_usage_and_existing_platform_parameters(monkeypatch):
    monkeypatch.setenv('QWEN38_LOCAL_MIN_MAX_TOKENS', '50000')
    monkeypatch.setenv('QWEN38_LOCAL_REASONING', 'low')
    payload = {'model': 'test', 'max_tokens': 900, 'chat_template_kwargs': {'enable_thinking': False}}
    original = copy.deepcopy(payload)
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        events = [
            {'choices': [{'delta': {'reasoning_content': 'check'}}]},
            {'choices': [{'delta': {'content': '{"ok":'}}]},
            {'choices': [{'delta': {'content': 'true}'}, 'finish_reason': 'stop'}]},
            {'choices': [], 'usage': {'total_tokens': 42}},
        ]
        data = '\n'.join('data: ' + json.dumps(e) for e in events) + '\ndata: [DONE]\n'
        return httpx.Response(200, text=data)
    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as http:
        result = client.stream_completion(http, 'http://test.invalid/v1/chat/completions', {}, payload, 17)
    assert requests == [{'model': 'test', 'max_tokens': 50000, 'stream': True,
                         'stream_options': {'include_usage': True}, 'reasoning_effort': 'low'}]
    assert result == {'choices': [{'message': {'content': '{"ok":true}', 'reasoning': 'check'},
                                  'finish_reason': 'stop'}], 'usage': {'total_tokens': 42}}
    assert payload == original


@pytest.mark.parametrize('body', ['data: {"error":{"message":"unavailable"}}\n', 'data: [DONE]\n'])
def test_stream_errors_are_not_returned_as_success(body):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)), trust_env=False) as http:
        with pytest.raises(RuntimeError):
            client.stream_completion(http, 'http://test.invalid/v1/chat/completions', {}, {})


@pytest.mark.parametrize('status, expected', [(400, 1), (429, 1), (503, 2)])
def test_only_existing_failover_statuses_try_another_endpoint(monkeypatch, status, expected):
    monkeypatch.setenv('QWEN38_LOCAL_STREAM', '0')
    monkeypatch.setattr(client, 'endpoint_key', lambda: '')
    monkeypatch.setattr(client, 'endpoint_order', lambda _: ['http://first.invalid/v1', 'http://second.invalid/v1'])
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(status)
    real_client = httpx.Client
    monkeypatch.setattr(client.httpx, 'Client', lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    with pytest.raises(httpx.HTTPStatusError):
        client.ask_json([], {}, name='review')
    assert len(requests) == expected


def test_explicit_judges_keep_endpoints_and_models_when_interleaved(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import os
    monkeypatch.setenv('QWEN38_LOCAL_MODEL', 'unrelated-default')
    monkeypatch.setenv('QWEN38_LOCAL_BASE_URL', 'http://unrelated.invalid/v1')
    original = {k: os.environ[k] for k in ['QWEN38_LOCAL_MODEL', 'QWEN38_LOCAL_BASE_URL']}
    settings = [client.JsonEndpoint('judge-a', ('http://a.invalid/v1',)),
                client.JsonEndpoint('judge-b', ('http://b.invalid/v1',))]
    def respond(request):
        payload = json.loads(request.content)
        assert payload['model'] == ('judge-a' if request.url.host == 'a.invalid' else 'judge-b')
        return httpx.Response(200, json={'choices':[{'message':{'content':json.dumps({'model':payload['model']})},'finish_reason':'stop'}]})
    real = httpx.Client
    monkeypatch.setattr(client.httpx, 'Client', lambda **kw: real(transport=httpx.MockTransport(respond), **kw))
    def ask(setting): return client.ask_json([], {}, name='isolation', settings=setting)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(ask, settings*4)) == [{'model':'judge-a'},{'model':'judge-b'}]*4
    assert original == {k: os.environ[k] for k in original}
