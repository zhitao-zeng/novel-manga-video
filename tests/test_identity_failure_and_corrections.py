import json
from pathlib import Path
import pytest

from novel_manga.application.identity import ledger_store as store, ledger_resolution as resolve, ledger_judges as judges
from novel_manga.application.review import episode as review_episode, evidence as review_evidence, judges as review_judges


def book(path, names):
    path.mkdir(parents=True)
    store.write_json(path / 'story_bible.json', {'novel_title': 'test', 'genre': 'test', 'visual_style': 'test',
        'palette': 'test', 'style_fingerprint': 'test', 'locations': [],
        'characters': [{'name': name, 'appearance': 'test', 'wardrobe': 'test'} for name in names]})
    ledger = store.Ledger(path)
    ledger.save()
    return ledger


def claim(cid, a, b, type='same_as'):
    return {'id': cid, 'chapter': 1, 'type': type, 'subject': a, 'object': b, 'status': 'pending',
            'scope': 'reality', 'hidden_from_reader': False, 'evidence': '两位角色相见。'}


def test_temporary_relink_failure_is_not_cached_or_written(tmp_path, monkeypatch):
    ledger = book(tmp_path / 'book', ['真实姓名角色'])
    before = {p.name: p.read_bytes() for p in ledger.base.glob('*.json')}
    raw = {'mentions': [{'form': '作家小姐', 'entity': 'e001', 'kind': 'proper', 'presence': 'on_stage',
                          'evidence': '作家小姐走进房间'}], 'new_entities': [], 'claims': [], 'relations': []}
    calls = []
    def reply(*args):
        calls.append(1)
        return {'entity': 'UNCERTAIN', 'error': True, 'why': 'error ConnectError'} if len(calls) == 1 else {
            'entity': 'e001', 'why': 'source confirms the same person'}
    monkeypatch.setattr(judges, 'judge_link', reply)
    with pytest.raises(RuntimeError, match='not committed'):
        resolve.resolve_chapter(ledger, 1, '作家小姐走进房间。', raw, workers=1)
    assert ledger.asked == {}
    assert not ledger.mentions_path(1).exists()
    assert {p.name: p.read_bytes() for p in ledger.base.glob('*.json')} == before
    resolve.resolve_chapter(ledger, 1, '作家小姐走进房间。', raw, workers=1)
    assert len(calls) == 2
    assert store.read_json(ledger.mentions_path(1), [])[0]['entity'] == 'e001'


def test_later_claim_failure_restores_memory_and_keeps_previous_chapter(tmp_path, monkeypatch):
    ledger = book(tmp_path / 'book', ['甲方角色', '乙方角色'])
    before = {str(p.relative_to(ledger.base)): p.read_bytes() for p in ledger.base.rglob('*.json')}
    raw = {'mentions': [], 'new_entities': [], 'relations': [], 'claims': [{
        **claim('ignored', 'e001', 'e002'), 'evidence': '甲方角色和乙方角色是同一个人'}]}
    monkeypatch.setattr(judges, 'judge_claim', lambda *args: {'verdict': 'insufficient', 'why': 'error TimeoutError', 'error': True})
    with pytest.raises(RuntimeError):
        resolve.resolve_chapter(ledger, 1, '甲方角色和乙方角色是同一个人。', raw, workers=1)
    assert ledger.claims == [] and ledger.mentions_by_chapter == {}
    assert {str(p.relative_to(ledger.base)): p.read_bytes() for p in ledger.base.rglob('*.json')} == before


def test_undo_keeps_other_manual_decision_and_merge(tmp_path):
    ledger = book(tmp_path / 'book', ['甲方角色', '乙方角色', '丙方角色'])
    ledger.claims = [claim('c1', 'e001', 'e002'), claim('c2', 'e001', 'e003')]
    assert resolve.decide(ledger, 'c1', 'accepted', 'first source confirmation')
    assert resolve.decide(ledger, 'c2', 'accepted', 'second source confirmation')
    assert resolve.decide(ledger, 'c1', 'rejected', 'first was mistaken')
    assert {c['claim']: c['decision'] for c in ledger.corrections} == {'c1': 'rejected', 'c2': 'accepted'}
    assert ledger.canonical('e002') == 'e002' and ledger.canonical('e003') == 'e001'
    assert [m['claim'] for m in ledger.merges] == ['c2']
    assert resolve._correction_for(ledger, claim('new', 'e001', 'e002'))['decision'] == 'rejected'


def test_body_decisions_remain_directional(tmp_path):
    ledger = book(tmp_path / 'book', ['甲方角色', '乙方角色'])
    ledger.corrections = [{**claim('c1', 'e001', 'e002', 'occupies_body'), 'decision': 'accepted'}]
    assert resolve._correction_for(ledger, claim('c2', 'e002', 'e001', 'occupies_body')) is None


def test_settle_does_not_erase_prior_manual_confirmation(tmp_path, monkeypatch):
    ledger = book(tmp_path / 'book', ['甲方角色', '乙方角色', '丙方角色'])
    ledger.claims = [claim('c1', 'e001', 'e002'), claim('c2', 'e001', 'e003')]
    resolve.decide(ledger, 'c1', 'accepted', 'manual confirmation stays')
    monkeypatch.setattr(judges, 'judge_settle', lambda *a: {'verdict': 'supports', 'why': 'source proof'})
    resolve.settle_pending(ledger, workers=1)
    assert any(c.get('claim') == 'c1' and c['by'] == 'human' for c in ledger.corrections)


def test_unchanged_video_is_rejudged_only_when_its_snapshot_changes(tmp_path, monkeypatch):
    ledger = book(tmp_path / 'book', ['旧身份角色', '正确身份角色'])
    ep = ledger.novel_dir / 'book_1'; ep.mkdir()
    store.write_json(ep / 'segments.json', [{'segment_id': 's1', 'text': '旧身份角色走进房间。'}])
    store.write_json(ledger.mentions_path(1), [{'entity': 'e001', 'form': '旧身份角色', 'kind': 'proper',
        'presence': 'on_stage', 'chapter': 1, 'evidence': '旧身份角色走进房间。'}])
    store.write_json(ep / 'clip_plan.json', {'clips': [{'clip_id': 'clip_01', 'kind': 'video', 'segment_ids': ['s1']}]})
    video = ep / 'work/clips/clip_01/attempt_01/clip.mp4'; video.parent.mkdir(parents=True); video.write_bytes(b'fixed take')
    calls = []
    monkeypatch.setenv('NOVEL_REVIEW_CAST_VIDEO', '0'); monkeypatch.setenv('NOVEL_REVIEW_MODE', 'classic')
    monkeypatch.setattr(review_judges, 'judge_clip', lambda *a, **kw: calls.append(1) or {'severity': 'pass'})
    first = review_episode.review_episode(ep, fresh=False)
    assert review_episode.review_episode(ep, fresh=False)['clips'] == first['clips'] and len(calls) == 1
    entities = store.read_json(ledger.base / 'entities.json', [])
    entities[0].update(status='merged', merged_into='e002')
    store.write_json(ledger.base / 'entities.json', entities)
    after = review_episode.review_episode(ep, fresh=False)
    assert len(calls) == 2 and '在场 正确身份角色' in after['clips']['clip_01']['ledger_snapshot']
    assert video.read_bytes() == b'fixed take'


def test_undo_replays_surviving_original_endpoints_not_old_union_roots(tmp_path):
    ledger = book(tmp_path / 'book', ['甲方角色', '乙方角色', '丙方角色', '丁方角色', '戊方角色'])
    ledger.claims = [claim('c1', 'e001', 'e002'), claim('c2', 'e002', 'e003'), claim('other', 'e004', 'e005')]
    for cid in ['c1', 'c2', 'other']:
        resolve.decide(ledger, cid, 'accepted', 'source confirmation')
    other = next(m.copy() for m in ledger.merges if m['claim'] == 'other')
    resolve.decide(ledger, 'c1', 'rejected', 'mistaken first proof')
    assert ledger.canonical('e001') != ledger.canonical('e002') == ledger.canonical('e003')
    assert other in ledger.merges
    assert next(c for c in ledger.corrections if c['claim'] == 'c2')['decision'] == 'accepted'


def test_conflicting_manual_proofs_are_reported_without_erasing_them(tmp_path):
    ledger = book(tmp_path / 'book', ['甲方角色', '乙方角色', '丙方角色'])
    ledger.claims = [claim('ab', 'e001', 'e002'), claim('ac', 'e001', 'e003'), claim('bc', 'e002', 'e003')]
    for cid in ['ab', 'ac', 'bc']:
        resolve.decide(ledger, cid, 'accepted', 'confirmed')
    before = (json.dumps(ledger.entities), json.dumps(ledger.corrections), json.dumps(ledger.merges))
    with pytest.raises(ValueError, match='conflicting proof'):
        resolve.decide(ledger, 'ab', 'rejected', 'conflicts with remaining confirmations')
    assert before == (json.dumps(ledger.entities), json.dumps(ledger.corrections), json.dumps(ledger.merges))
