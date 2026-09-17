import json
import httpx
from novel_manga.bible import BibleBuilder
from novel_manga.config import Settings


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
