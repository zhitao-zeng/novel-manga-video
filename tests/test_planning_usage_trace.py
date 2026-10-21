import importlib
import json

import httpx
import pytest

from novel_manga.llm import transport, usage
from novel_manga.llm.config import JsonEndpoint


def rows(root):
    return [json.loads(line) for line in (root/'.runtime/llm-usage.jsonl').read_text().splitlines()]


def test_stream_usage_keeps_actual_gateway_and_response_ids(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    event = {'id': 'completion-1', 'choices': [{'delta': {'content': '{}'}, 'finish_reason': 'stop'}],
             'usage': {'prompt_tokens': 100, 'completion_tokens': 5, 'total_tokens': 105,
                       'prompt_tokens_details': {'cached_tokens': 80}}}
    response = 'data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n'
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, headers={'x-phanthy-request-id': 'gateway-1'}, text=response)))
    settings = JsonEndpoint('deepseek-v4.1-flash', ('http://fake.invalid/v1',), stream=True, min_max_tokens=0)
    with usage.scope(novel='book', episode=17, stage='planning_repair'):
        transport.post_any(client, list(settings.endpoints), {}, {'model': settings.model}, settings=settings)
    row = rows(tmp_path)[0]
    assert row['response_id'] == 'completion-1' and row['request_id'] == 'gateway-1'
    assert row['cache_read_input_tokens'] == 80 and row['usage_known'] is True
    assert row['episode'] == 17 and row['novel'] == 'book'


def test_http_credit_failure_is_recorded_with_unknown_usage_not_zero_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        402, headers={'x-phanthy-request-id': 'gateway-402'}, json={'error': {'code': 'insufficient_credits'}})))
    settings = JsonEndpoint('deepseek-v4.1-flash', ('http://fake.invalid/v1',), min_max_tokens=0)
    with pytest.raises(httpx.HTTPStatusError):
        transport.post_any(client, list(settings.endpoints), {}, {'model': settings.model}, settings=settings)
    row = rows(tmp_path)[0]
    assert row['request_id'] == 'gateway-402' and row['http_status'] == 402
    assert row['usage_known'] is False
    assert 'prompt_tokens' not in row and 'completion_tokens' not in row


def test_native_usage_prefers_final_transcript_and_deduplicates_message_ids(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    module = importlib.import_module('novel_manga.llm.native_usage')
    run = tmp_path/'agent';attempt = run/'attempts/one';attempt.mkdir(parents=True)
    message = {'type': 'assistant', 'session_id': 'session-1', 'message': {'id': 'native-1',
        'usage': {'input_tokens': 999, 'output_tokens': 0}}}
    (attempt/'session.jsonl').write_text(json.dumps(message)+'\n'+json.dumps(message)+'\n')
    transcript = run/'.agent-home/.claude/projects/-work/session-1.jsonl';transcript.parent.mkdir(parents=True)
    final = {'type': 'assistant', 'message': {'id': 'native-1', 'usage': {
        'input_tokens': 100, 'output_tokens': 20, 'cache_read_input_tokens': 80}}}
    transcript.write_text(json.dumps(final)+'\n')
    with usage.scope(novel='book', episode=17, stage='planning_repair'):
        module.record_attempt(attempt, run_dir=run, model='deepseek-v4.1-flash', url='http://local')
        module.record_attempt(attempt, run_dir=run, model='deepseek-v4.1-flash', url='http://local')
    recorded = rows(tmp_path)
    assert len(recorded) == 1
    assert recorded[0]['input_tokens'] == 100 and recorded[0]['output_tokens'] == 20
    assert recorded[0]['response_id'] == 'native-1'
    assert recorded[0]['usage_source'] == 'cli_transcript'
    assert recorded[0]['input_semantics'] == 'provider_native_unverified'


def test_failed_native_attempt_without_response_ids_does_not_invent_model_call_count(tmp_path, monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT', str(tmp_path))
    module = importlib.import_module('novel_manga.llm.native_usage')
    run = tmp_path/'agent';attempt = run/'attempts/one';attempt.mkdir(parents=True)
    (attempt/'session.jsonl').write_text(json.dumps({'type': 'result', 'is_error': True})+'\n')
    module.record_attempt(attempt, run_dir=run, model='deepseek-v4.1-flash', url='http://local')
    row = rows(tmp_path)[0]
    assert row['event_type'] == 'agent_attempt' and row['usage_known'] is False
    assert 'input_tokens' not in row and 'output_tokens' not in row


def test_codex_native_attempt_already_metered_by_adapter_does_not_add_unknown_sdk_call(tmp_path,monkeypatch):
    monkeypatch.setenv('NOVEL_PROJECT_ROOT',str(tmp_path))
    module=importlib.import_module('novel_manga.llm.native_usage')
    run=tmp_path/'agent';attempt=run/'attempts/one';attempt.mkdir(parents=True)
    (attempt/'session.jsonl').write_text('{"type":"turn.completed"}\n')
    (attempt/'execution.json').write_text(json.dumps({'backend':'codex-chatgpt-cli',
        'actual_model':'gpt-6-luna','usage_recorded':True,'usage':{'input_tokens':100,'output_tokens':10}}))
    result=module.record_attempt(attempt,run_dir=run,model='original-ds',url='http://unused')
    assert result.get('already_metered') is True
    assert not(tmp_path/'.runtime/llm-usage.jsonl').exists()
