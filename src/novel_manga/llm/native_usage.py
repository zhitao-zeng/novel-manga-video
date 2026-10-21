"""Read actual native Agent message usage; transcript backups are not new calls."""
from pathlib import Path
import json

from novel_manga.llm import usage
from novel_manga.util import read_json, atomic_write_json


def _rows(path):
    if not path.is_file():
        return
    with path.open() as stream:
        for line in stream:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def record_attempt(attempt_dir, *, run_dir, model, url):
    attempt_dir, run_dir = Path(attempt_dir), Path(run_dir)
    native = read_json(attempt_dir/'execution.json', {})
    if native.get('backend') == 'codex-chatgpt-cli' and native.get('usage_recorded'):
        return {'already_metered': True, 'backend': 'codex-chatgpt-cli', 'usage': native.get('usage', {})}
    path = attempt_dir/'session.jsonl'
    if not path.is_file():
        return {'recorded_responses': 0, 'session_missing': True}
    messages, sessions = {}, set()
    for row in _rows(path):
        if row.get('session_id'):
            sessions.add(row['session_id'])
        message = row.get('message') or {}
        if row.get('type') == 'assistant' and message.get('id'):
            key = message['id']
            if key not in messages or message.get('usage'):
                messages[key] = {'message': message, 'source': 'cli_stream_unverified'}
    for session in sessions:
        transcript = run_dir/'.agent-home/.claude/projects/-work'/f'{session}.jsonl'
        for row in _rows(transcript):
            message = row.get('message') or {}
            if message.get('id') in messages and message.get('usage'):
                messages[message['id']] = {'message': message, 'source': 'cli_transcript'}
    receipt_path = run_dir/'usage-recorded.json'
    previous = read_json(receipt_path, {})
    known = set(previous.get('message_ids') or [])
    unknown = set(previous.get('unidentified_attempts') or [])
    count = 0
    for key, item in messages.items():
        if key in known:
            continue
        message = item['message'];tokens = message.get('usage')
        with usage.scope(response_id=key, attempt=str(attempt_dir), protocol='messages',
                usage_source=item['source'], input_semantics='provider_native_unverified',
                usage_known=bool(tokens), served_model=message.get('model'), event_type='model_response'):
            recorded = usage.record(name='sandbox-agent', model=model, usage=tokens, url=url)
        if recorded:
            count += 1;known.add(key)
    if not messages and str(attempt_dir) not in unknown:
        with usage.scope(attempt=str(attempt_dir), protocol='messages', usage_known=False,
                         event_type='agent_attempt', status='incomplete'):
            recorded = usage.record(name='sandbox-agent', model=model, usage=None, url=url)
        if recorded:
            unknown.add(str(attempt_dir))
    atomic_write_json(receipt_path, {'message_ids': sorted(known), 'unidentified_attempts': sorted(unknown)})
    return {'recorded_responses': count, 'unidentified_attempts': len(unknown)}
