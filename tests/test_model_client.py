"""Transport regressions shared by planning, source identity and review."""
import copy
import json
import httpx
import pytest
from novel_manga.llm import client as client
from novel_manga.llm import transport
from novel_manga.llm.config import JsonEndpoint


def test_explicit_ds_refusal_uses_qwen_once_inside_same_deadline(monkeypatch):
    monkeypatch.setenv('NOVEL_REFUSAL_FALLBACK', 'flashnext')
    calls = []
    def respond(http, bases, headers, payload, **kwargs):
        calls.append((payload['model'], kwargs['deadline']))
        content = 'I cannot help with this request.' if len(calls) == 1 else '{"consistent":true}'
        return {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]}
    monkeypatch.setattr(client, 'post_any', respond)
    answer = client.ask_json([], {}, name='source', settings=JsonEndpoint('DeepSeek-V4.1-Flash', ('http://ds/v1',)))
    assert answer == {'consistent': True}
    assert [c[0] for c in calls] == ['DeepSeek-V4.1-Flash', 'Qwen3.8-Flash-Next']
    assert calls[0][1] == calls[1][1]


def test_negative_source_verdict_does_not_trigger_model_fallback(monkeypatch):
    monkeypatch.setenv('NOVEL_REFUSAL_FALLBACK', 'flashnext')
    calls = []
    def respond(*args, **kwargs):
        calls.append(1)
        return {'choices': [{'message': {'content': '{"consistent":false,"problems":["wrong speaker"]}'}, 'finish_reason': 'stop'}]}
    monkeypatch.setattr(client, 'post_any', respond)
    assert client.ask_json([], {}, name='source', settings=JsonEndpoint('DeepSeek', ('http://ds/v1',)))['consistent'] is False
    assert len(calls) == 1


def test_json_object_mode_sends_schema_as_text_without_changing_original_parts(monkeypatch):
    parts = [{'type': 'text', 'text': 'Write two shots.'}]
    schema = {'type': 'object', 'properties': {'shots': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}}}}
    saved = copy.deepcopy(parts)
    seen = []
    def respond(http, bases, headers, payload, **kwargs):
        seen.append(payload)
        return {'choices': [{'message': {'content': '{"shots":["A walks.","B sits."]}'}, 'finish_reason': 'stop'}]}
    monkeypatch.setattr(client, 'post_any', respond)
    assert client.ask_json(parts, schema, name='h3', json_object=True)['shots'] == ['A walks.', 'B sits.']
    assert seen[0]['response_format'] == {'type': 'json_object'}
    assert json.dumps(schema) in seen[0]['messages'][0]['content'][-1]['text']
    assert parts == saved


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
        result = transport.stream_completion(http, 'http://test.invalid/v1/chat/completions', {}, payload, 17)
    assert requests == [{'model': 'test', 'max_tokens': 50000, 'stream': True,
                         'stream_options': {'include_usage': True}, 'reasoning_effort': 'low'}]
    assert result == {'choices': [{'message': {'content': '{"ok":true}', 'reasoning': 'check'},
                                  'finish_reason': 'stop'}], 'usage': {'total_tokens': 42}}
    assert payload == original


@pytest.mark.parametrize('body', ['data: {"error":{"message":"unavailable"}}\n', 'data: [DONE]\n'])
def test_stream_errors_are_not_returned_as_success(body):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)), trust_env=False) as http:
        with pytest.raises(RuntimeError):
            transport.stream_completion(http, 'http://test.invalid/v1/chat/completions', {}, {})


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
    settings = [JsonEndpoint('judge-a', ('http://a.invalid/v1',)),
                JsonEndpoint('judge-b', ('http://b.invalid/v1',))]
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
