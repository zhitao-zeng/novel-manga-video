"""Report, history and publication retain their original ordering and cache reuse."""
import json
from pathlib import Path
import pytest
import novel_manga.application.repair.review_flow as flow
import novel_manga.application.repair.history as history
import novel_manga.application.repair.delivery as delivery
import novel_manga.application.review.verify as verifier_module
from support.repair_history import episode, candidate_episode
from novel_manga.review.contracts import POLICY
from novel_manga.util import read_json


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
                    'policy':POLICY,'review_mode':'verify',
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


def test_old_mode_evidence_is_rechecked_before_candidate_publication(episode,tmp_path,monkeypatch):
    from novel_manga.application.review import store
    directory,plan,prior,media,takes=candidate_episode(episode)
    prior.update(policy='old-policy',review_mode='verify',feedback={})
    prior['clips']['clip_01'].update(severity='pass',tier='optional',story_ok=None,verify={'verdict':'fine'})
    (directory/'episode_review.json').write_text(json.dumps(prior))
    reconciled,_=store.reconcile(directory,{}, {})
    assert reconciled.get('review_mode') is None
    assert delivery.publish_if_ready(directory,reconciled,takes) is False
    assert (directory/'nov_1.mp4').read_bytes()==b'incumbent movie'
    calls=[]
    class Verifier:
        def __init__(self,*a,**kw):pass
        def verify(self,job):
            calls.append(job)
            return {**takes['clip_01'],'ep':1,'clip':'clip_01','mode':job[-1],
                    'policy':POLICY,'review_mode':'verify','verdict':'fine','people':[],'evidence':'actual new check'}
    monkeypatch.setattr(verifier_module,'CurrentVerifier',Verifier)
    result=flow.review_batch(directory.parent,[1],'all',tmp_path/'state',tmp_path/'legacy')
    assert result['judged']==1 and result['remaining']==0 and len(calls)==1
    assert read_json(directory/'episode_review.json')['review_mode']=='verify'
    assert (directory/'nov_1.mp4').read_bytes()==b'new candidate'


@pytest.mark.parametrize('specific_result,primary_verdict,can_publish',[
    ('resolved','fine',True),('unresolved','fine',False),('resolved','obvious',False),
])
@pytest.mark.parametrize('cached_primary',[True,False])
def test_confirmed_claim_check_keeps_the_batch_verify_mode(
        episode,tmp_path,monkeypatch,specific_result,primary_verdict,can_publish,cached_primary):
    from novel_manga.application.review import confirmed
    directory,_,review,_,takes=candidate_episode(episode)
    review.update(policy=POLICY,review_mode='verify',feedback={})
    review['clips']['clip_01'].update(
        story_ok=True,verify={'verdict':'fine'},
        confirmed={'issue':'old claim','instruction':'one lead',
                   'video':'old-video.mp4','take':[1,2,3]})
    if not cached_primary:
        review['clips']['clip_01'].update(video='old-video.mp4',take=[1,2,3])
    (directory/'episode_review.json').write_text(json.dumps(review))
    state=tmp_path/'state'
    state.mkdir()
    record={**takes['clip_01'],'ep':1,'clip':'clip_01','mode':'joint',
            'policy':POLICY,'review_mode':'verify','verdict':primary_verdict,
            'people':[],'evidence':'primary current check','instruction':'primary correction'}
    if cached_primary:
        (state/'verified.jsonl').write_text(json.dumps(record)+'\n')
    primary_calls=[]
    class Verifier:
        def __init__(self,*a,**kw):pass
        def verify(self,job):
            primary_calls.append(job)
            return record
    monkeypatch.setattr(verifier_module,'CurrentVerifier',Verifier)
    monkeypatch.setenv('NOVEL_REVIEW_MODE','classic')
    monkeypatch.setattr('novel_manga.application.review.episode.review_episode',
                        lambda *a,**kw:pytest.fail('do not switch the batch to a different primary mode'))
    calls=[]
    def check(*args):
        calls.append(1)
        return {'result':specific_result,'observations':['one lead'],'evidence':'specific current check'}
    monkeypatch.setattr(confirmed,'judge_finding',check)
    result=flow.review_batch(directory.parent,[1],'all',state,tmp_path/'legacy')
    assert result['judged']==len(primary_calls)==int(not cached_primary)
    assert result['remaining']==0 and calls==[1]
    saved=read_json(directory/'episode_review.json')
    assert saved['review_mode']=='verify'
    assert saved['clips']['clip_01']['confirmed']['check']['result']==specific_result
    assert bool(saved['feedback']) is not can_publish
    expected=b'new candidate' if can_publish else b'incumbent movie'
    assert (directory/'nov_1.mp4').read_bytes()==expected


def test_reconciliation_and_queue_do_not_depend_on_executors():
    import ast
    root = Path(__file__).resolve().parents[1]
    high = {'novel_manga.application.repair.review', 'novel_manga.application.review.audit_cli', 'thin_batch', 'manage_repair_thin',
            'novel_manga.application.repair.review_flow', 'novel_manga.application.review.audit_flow', 'novel_manga.application.review.verify', 'novel_manga.application.repair.delivery'}
    paths = [root / 'src/novel_manga/review/reconciliation.py', root / 'src/novel_manga/review/audit_queue.py',
             root / 'src/novel_manga/application/review/store.py', root / 'src/novel_manga/application/repair/history.py']
    def imports(path):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import): yield from (alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module: yield node.module
    for path in paths:
        assert not (set(imports(path)) & high), path
    for base in ['scripts', 'src', 'experiments']:
        for path in (root / base).rglob('*.py'):
            if path in {root / 'scripts/repair_review_thin.py', root / 'scripts/shared_audit_thin.py'}:
                continue  # public commands invoke their own CLI implementation
            assert not (set(imports(path)) & {'novel_manga.application.repair.review', 'novel_manga.application.review.audit_cli'}), path
