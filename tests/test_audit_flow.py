"""Exercise existing audit completion/retry/stop transitions without a model service."""
from concurrent.futures import Future
import copy
import json
import signal
import pytest
from novel_manga.review import audit_queue
from novel_manga.util import load_dotenv
import novel_manga.application.review.audit_flow as flow
import novel_manga.application.review.verify as verifier_module


class ImmediateExecutor:
    """Run the worker deterministically; concurrent SQL claims have separate tests."""
    def __init__(self, **kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def submit(self, operation):
        future = Future()
        try: future.set_result(operation())
        except BaseException as error: future.set_exception(error)
        return future


@pytest.mark.parametrize('case, code, counts, calls', [
    ('success', 0, {'done': 1}, 1),
    ('retry', 0, {'done': 1}, 2),
    ('error', 4, {'error': 1}, 2),
    ('stale_before', 0, {'superseded': 1}, 0),
    ('stale_after', 0, {'superseded': 1}, 1),
    ('stop', 2, {'done': 1, 'pending': 1}, 1),
])
def test_audit_keeps_existing_task_transitions(tmp_path, monkeypatch, case, code, counts, calls):
    novel = tmp_path / 'book'; state = novel / 'repair_manager'; queue = state / 'shared_audit.sqlite3'
    targets = [{'episode': n, 'clip': 'a', 'video': str(novel / f'{n}.mp4'), 'take': [n, 10, 100]}
               for n in range(1, 3 if case == 'stop' else 2)]
    audit_queue.initialize(queue, targets)
    signal_handlers, requests, options = {}, [], []
    monkeypatch.setattr(flow.signal, 'signal', lambda sig, handler: signal_handlers.update({sig: handler}))
    monkeypatch.setattr(flow, 'ThreadPoolExecutor', ImmediateExecutor)
    class FakeVerifier:
        def __init__(self, *args, **kwargs): options.append((args[2:], kwargs))
        def verify(self, job):
            requests.append(job)
            if case == 'stop': signal_handlers[signal.SIGTERM]()
            row = targets[job[0] - 1]
            record = {'ep': job[0], 'clip': 'a', 'video': row['video'], 'take': row['take'],
                      'mode': 'joint', 'verdict': 'fine', 'people': []}
            if case == 'error' or case == 'retry' and len(requests) == 1:
                record['error'] = 'model timeout'
            return record
    monkeypatch.setattr(verifier_module, 'CurrentVerifier', FakeVerifier)
    inspections = []
    def current(novel, episode, cid):
        inspections.append(episode)
        if case == 'stale_before': return None
        row = targets[episode - 1]
        return {'video': row['video'], 'take': row['take'],
                'plan_clip': {'changed': case == 'stale_after' and len(inspections) > 1}}
    monkeypatch.setattr(flow, 'current', current)
    assert flow.run(novel, state, queue, 'qwen', 1, max_tokens=777) == code
    assert audit_queue.summary(queue)['counts'] == counts
    assert len(requests) == calls
    assert options == [(('shared_qwen', 1), {'repair_advice': False, 'max_tokens': 777})]
    with audit_queue.connect(queue) as db:
        attempts = [r[0] for r in db.execute('SELECT attempts FROM checks ORDER BY id')]
    assert attempts == ([1, 0] if case == 'stop' else [2 if case in {'retry', 'error'} else 1])
    status = json.loads((state / 'audit_status_qwen.json').read_text())
    assert status['status'] == ('stopped' if case == 'stop' else 'complete_with_errors' if case == 'error' else 'complete')
    assert not (state / 'history.json').exists()


def test_shared_environment_loader_keeps_explicit_settings(tmp_path, monkeypatch):
    monkeypatch.setenv('NMV_TEST_PRESENT', 'from process')
    monkeypatch.delenv('NMV_TEST_NEW', raising=False)
    config = tmp_path / '.env'
    config.write_text('# note\nNMV_TEST_PRESENT=from file\nexport NMV_TEST_NEW="new value"\ninvalid line\n')
    load_dotenv(config)
    import os
    assert os.environ['NMV_TEST_PRESENT'] == 'from process'
    assert os.environ['NMV_TEST_NEW'] == 'new value'
    load_dotenv(tmp_path / 'missing')
