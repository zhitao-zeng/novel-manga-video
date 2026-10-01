import json
import time
import httpx
import pytest
from novel_manga.bible import BibleBuilder
from novel_manga.config import Settings
from novel_manga.llm.resources import model_slot


def test_bible_retains_context_retry_and_punctuation_tolerance():
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(400, json={'error': {'message':
                'maximum context length is 12000 tokens; prompt contains at least 4000 input tokens'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"a":1 "b":2}'}}]})
    settings = Settings(llm_base_url='http://model.invalid/v1', llm_api_key='test-only',
                        llm_model='bible-test', llm_max_tokens=16000, llm_disable_thinking=True)
    builder = BibleBuilder(settings); builder.client.close()
    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        builder.client = http
        assert builder._json('system', 'source') == {'a': 1, 'b': 2}
    assert len(requests) == 2
    first, second = requests
    assert first['response_format'] == {'type': 'json_object'}
    assert first['temperature'] == .2 and first['model'] == 'bible-test'
    assert first['messages'] == [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'source'}]
    assert first['max_tokens'] == 16000 and second['max_tokens'] == 6976
    assert {k: v for k, v in first.items() if k != 'max_tokens'} == {k: v for k, v in second.items() if k != 'max_tokens'}


@pytest.fixture
def shared_bible_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_MODEL_RESOURCES', '[{"base":"http://model.invalid","slots":1}]')
    monkeypatch.setenv('NOVEL_MODEL_INFLIGHT_DIR', str(tmp_path / 'model-slots'))
    return Settings(llm_base_url='http://model.invalid/v1', llm_api_key='test-only',
                    llm_model='bible-test', llm_max_tokens=16000, request_timeout=37)


def test_bible_initial_and_context_retry_each_hold_shared_slot(shared_bible_endpoint):
    requests = []

    def handle(request):
        requests.append(request)
        with pytest.raises(TimeoutError):
            with model_slot('http://model.invalid', deadline=time.monotonic() + .01):
                pytest.fail('bible request was not admitted through its shared slot')
        if len(requests) == 1:
            return httpx.Response(400, json={'error': {'message':
                'maximum context length is 12000 tokens; prompt contains at least 4000 input tokens'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})

    builder = BibleBuilder(shared_bible_endpoint)
    builder.client.close()
    with httpx.Client(transport=httpx.MockTransport(handle), timeout=37, trust_env=False) as http:
        builder.client = http
        assert builder._json('system', 'source') == {'ok': True}
    assert len(requests) == 2
    assert [json.loads(r.content)['max_tokens'] for r in requests] == [16000, 6976]
    assert all(request.extensions['timeout']['read'] == 37 for request in requests)
    with model_slot('http://model.invalid/v1', deadline=time.monotonic() + .2):
        pass


@pytest.mark.parametrize('transport_error', [False, True])
def test_bible_failed_request_releases_shared_slot(shared_bible_endpoint, transport_error):
    def handle(request):
        with pytest.raises(TimeoutError):
            with model_slot('http://model.invalid', deadline=time.monotonic() + .01):
                pytest.fail('bible request bypassed the shared slot')
        if transport_error:
            raise httpx.ConnectError('simulated connection failure', request=request)
        return httpx.Response(500, text='simulated backend error')

    builder = BibleBuilder(shared_bible_endpoint)
    builder.client.close()
    with httpx.Client(transport=httpx.MockTransport(handle), trust_env=False) as http:
        builder.client = http
        with pytest.raises(httpx.ConnectError if transport_error else RuntimeError):
            builder._json('system', 'source')
    with model_slot('http://model.invalid/v1', deadline=time.monotonic() + .2):
        pass
