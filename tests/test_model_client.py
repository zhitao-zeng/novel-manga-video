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


@pytest.mark.parametrize('model,template,explicit,expected', [
    ('deepseek-v4.1-flash', {'enable_thinking': False}, None, {'type': 'disabled'}),
    ('deepseek-v4.1-flash', {'enable_thinking': True}, None, None),
    ('deepseek-v4.1-flash', None, None, None),
    ('phanthy.com/phancy-35b', {'enable_thinking': False}, None, None),
    ('glm-5.3-flash', {'enable_thinking': False}, None, None),
    ('deepseek-v4.1-flash', {'enable_thinking': False}, {'type': 'enabled'}, {'type': 'enabled'}),
])
def test_stream_preserves_supported_ds_non_thinking_intent_without_raising_budget(model, template, explicit, expected):
    import copy
    from novel_manga.llm import transport
    from novel_manga.llm.config import JsonEndpoint
    sent = []
    def respond(request):
        sent.append(json.loads(request.content))
        event = {'choices': [{'delta': {'content': '{"ok":true}'}, 'finish_reason': 'stop'}]}
        return httpx.Response(200, text='data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n')
    payload = {'model': model, 'max_tokens': 200, 'messages': []}
    if template is not None: payload['chat_template_kwargs'] = template
    if explicit is not None: payload['thinking'] = explicit
    before = copy.deepcopy(payload)
    settings = JsonEndpoint(model, ('http://test.invalid/v1',), '', stream=True, min_max_tokens=0)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        answer = transport.stream_completion(client, 'http://test.invalid/v1/chat/completions', {}, payload, 17, settings=settings)
    assert sent[0].get('thinking') == expected
    assert sent[0]['max_tokens'] == 200 and payload == before
    assert answer['choices'][0]['finish_reason'] == 'stop'


def _gateway_error(status):
    request = httpx.Request('POST', 'http://gateway.invalid/v1/chat/completions')
    return httpx.HTTPStatusError('gateway response', request=request, response=httpx.Response(status, request=request))


def test_single_gateway_retry_keeps_request_budget_and_valid_failure(monkeypatch):
    seen = []
    def post(http, bases, headers, payload, **kw):
        seen.append((copy.deepcopy(payload), kw['deadline']))
        if len(seen) == 1: raise _gateway_error(502)
        return {'choices': [{'message': {'content': '{"consistent":false,"problems":["wrong speaker"]}'}, 'finish_reason': 'stop'}]}
    monkeypatch.setattr(client, 'post_any', post)
    result = client.ask_json([], {}, name='source', max_tokens=700, settings=JsonEndpoint('test', ('http://gateway.invalid/v1',)))
    assert result['consistent'] is False and len(seen) == 2
    assert seen[0][0] == seen[1][0] and seen[0][1] is seen[1][1]


def test_gateway_transport_failures_use_at_most_existing_three_rounds(monkeypatch):
    seen = []
    def post(*a, **kw):
        seen.append(kw['deadline'])
        if len(seen) == 1: raise httpx.ConnectError('disconnected')
        raise _gateway_error(504)
    monkeypatch.setattr(client, 'post_any', post)
    with pytest.raises(httpx.HTTPStatusError):
        client.ask_json([], {}, name='source', settings=JsonEndpoint('test', ('http://gateway.invalid/v1',)))
    assert len(seen) == 3 and all(d is seen[0] for d in seen)


@pytest.mark.parametrize('status', [400, 401, 402, 403, 429])
def test_non_transient_gateway_error_does_not_retry(monkeypatch, status):
    seen = []
    def post(*a, **kw):
        seen.append(1); raise _gateway_error(status)
    monkeypatch.setattr(client, 'post_any', post)
    with pytest.raises(httpx.HTTPStatusError):
        client.ask_json([], {}, name='source', settings=JsonEndpoint('test', ('http://gateway.invalid/v1',)))
    assert seen == [1]


@pytest.mark.parametrize('settings,allow_retry', [(JsonEndpoint('test', ('http://a/v1','http://b/v1')), True), (JsonEndpoint('test', ('http://a/v1',)), False)])
def test_gateway_retry_does_not_multiply_endpoint_pool_or_explicit_single_attempt(monkeypatch, settings, allow_retry):
    seen = []
    def post(*a, **kw):
        seen.append(1); raise _gateway_error(503)
    monkeypatch.setattr(client, 'post_any', post)
    with pytest.raises(httpx.HTTPStatusError):
        client.ask_json([], {}, name='source', settings=settings, retry_truncated=allow_retry)
    assert seen == [1]


@pytest.mark.parametrize('reasoning_only', [False, True])
def test_gateway_retry_cannot_reuse_expanded_or_pooled_truncation_allocation(monkeypatch, reasoning_only):
    seen = []
    def post(http, bases, headers, payload, **kw):
        seen.append(payload['max_tokens'])
        if len(seen) == 1:
            return {'choices': [{'message': {'content': '' if reasoning_only else '{'}, 'finish_reason': 'length'}],
                    'usage': {'completion_tokens_details': {'reasoning_tokens': 1600 if reasoning_only else 0}}}
        raise _gateway_error(502)
    monkeypatch.setattr(client, 'post_any', post)
    with pytest.raises(httpx.HTTPStatusError):
        client.ask_json([], {}, name='source', max_tokens=1600, settings=JsonEndpoint('test', ('http://a/v1',)))
    assert seen == [1600, 4800 if reasoning_only else 3200]


def test_gateway_retry_does_not_restart_expired_deadline(monkeypatch):
    seen = []
    def post(*a, **kw):
        seen.append(1)
        kw['deadline'].remaining = lambda: 0
        raise _gateway_error(502)
    monkeypatch.setattr(client, 'post_any', post)
    with pytest.raises(httpx.HTTPStatusError):
        client.ask_json([], {}, name='source', settings=JsonEndpoint('test', ('http://a/v1',)))
    assert seen == [1]
