"""Report, history and publication retain their original ordering and cache reuse."""
import json
from pathlib import Path
import repair_review_flow_thin as flow
import repair_history as history
import repair_delivery_thin as delivery
import verify_clips_thin as verifier_module
from test_repair_history import episode, candidate_episode


def test_review_persists_before_history_and_publication(episode, tmp_path, monkeypatch):
    directory, plan, prior, media, takes = candidate_episode(episode)
    # A legacy candidate still awaiting its first precise verdict.
    prior['clips']['clip_01'].pop('verify')
    (directory / 'episode_review.json').write_text(json.dumps(prior))
    target = takes['clip_01']
    requests, order, writes = [], [], []
    class FakeVerifier:
        def __init__(self, *args, **kwargs):
            assert args[2:] == ('local', 2) and kwargs == {'max_tokens': 543}
        def verify(self, job):
            requests.append(job)
            return {**target, 'ep': 1, 'clip': 'clip_01', 'mode': job[-1],
                    'verdict': 'fine', 'people': [], 'evidence': 'current take is correct'}
    monkeypatch.setattr(verifier_module, 'CurrentVerifier', FakeVerifier)
    original_observe, original_publish = history.observe, delivery.publish_if_ready
    def observe(ep, review, current):
        assert json.loads((ep / 'episode_review.json').read_text()) == review
        order.append('observe')
        original_observe(ep, review, current)
    def publish(ep, review, current):
        assert history.load(ep)['observations']['clip_01'][-1]['verdict'] == 'fine'
        order.append('publish')
        return original_publish(ep, review, current)
    monkeypatch.setattr(history, 'observe', observe)
    monkeypatch.setattr(delivery, 'publish_if_ready', publish)
    original_copy, original_write = delivery.copy_complete, delivery.atomic_write_json
    def copy_complete(source, target):
        writes.append(target.name); original_copy(source, target)
    def write_json(path, value):
        writes.append(path.name); original_write(path, value)
    monkeypatch.setattr(delivery, 'copy_complete', copy_complete)
    monkeypatch.setattr(delivery, 'atomic_write_json', write_json)
    before = history.load(directory)
    state, legacy = tmp_path / 'state', tmp_path / 'legacy'
    result = flow.review_batch(directory.parent, [1], 'all', state, legacy, workers=2, max_tokens=543)
    assert result == {'episodes': [1], 'scope': 'all', 'judged': 1, 'remaining': 0}
    assert requests == [(1, 'clip_01', '', 'managed')] and order == ['observe', 'publish']
    assert writes == ['previous_final.mp4', 'publication.json', 'nov_1.mp4', 'thin_media_report.json']
    assert history.load(directory)['trials'] == before['trials']
    assert (directory / 'nov_1.mp4').read_bytes() == b'new candidate'
    assert (directory / 'repair_history/previous_final.mp4').read_bytes() == b'incumbent movie'
    record_bytes = (state / 'verified.jsonl').read_bytes()
    monkeypatch.setattr(verifier_module, 'CurrentVerifier', lambda *a, **k: (_ for _ in ()).throw(AssertionError('same take must be reused')))
    result = flow.review_batch(directory.parent, [1], 'all', state, legacy)
    assert result['judged'] == result['remaining'] == 0
    assert (state / 'verified.jsonl').read_bytes() == record_bytes
    assert len(history.load(directory)['observations']['clip_01']) == 1


def test_reconciliation_and_queue_do_not_depend_on_executors():
    import ast
    root = Path(__file__).resolve().parents[1]
    high = {'repair_review_thin', 'shared_audit_thin', 'thin_batch', 'manage_repair_thin',
            'repair_review_flow_thin', 'audit_flow_thin', 'verify_clips_thin', 'repair_delivery_thin'}
    paths = [root / 'src/novel_manga/review/reconciliation.py', root / 'src/novel_manga/review/audit_queue.py',
             root / 'scripts/review_store_thin.py', root / 'scripts/repair_history.py']
    def imports(path):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import): yield from (alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module: yield node.module
    for path in paths:
        assert not (set(imports(path)) & high), path
    for base in ['scripts', 'src', 'experiments']:
        for path in (root / base).rglob('*.py'):
            assert not (set(imports(path)) & {'repair_review_thin', 'shared_audit_thin'}), path
