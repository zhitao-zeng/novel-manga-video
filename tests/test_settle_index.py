"""Evidence is identical to the disk-scanning baseline, including merged aliases and relations."""
import copy

import pytest

from novel_manga.application.identity import ledger_store as store, ledger_resolution as resolve, ledger_judges
from support.ledger_evidence import full_scan_evidence_pack


def make_ledger(path, merged=False):
    base = store.store(path)
    rows = [{'id': eid, 'canonical': name, 'kind': 'person', 'status': status,
             'merged_into': target, 'role': '', 'description': '', 'named': True, 'source': 'test'}
            for eid, name, status, target in [('e001', '甲郎', 'active', None),
                ('e002', '乙郎', 'active', None), ('e003', '甲郎旧称', 'merged' if merged else 'active',
                                               'e001' if merged else None), ('e004', '闲客', 'active', None)]]
    store.write_json(base / 'entities.json', rows)
    mentions = {1: [('e001', '甲郎'), ('e002', '乙郎')], 2: [('e002', '乙郎')],
                4: [('e004', '闲客')], 7: [('e003', '甲郎旧称')]}
    for chapter, people in mentions.items():
        store.write_json(base / 'mentions' / f'ch_{chapter:04d}.json', [
            {'entity': eid, 'form': name, 'chapter': chapter, 'kind': 'proper',
             'presence': 'on_stage', 'evidence': f'{name}在第{chapter}章出现。'} for eid, name in people])
    store.write_json(base / 'relations/ch_0004.json', [{'from': 'e001', 'to': 'e004',
        'chapter': 4, 'base': 'unknown', 'stance': 'neutral', 'address': '老友'}])
    ledger = store.Ledger(path)
    ledger.claims = [{'id': 'c0001-01', 'chapter': 1, 'type': 'same_as', 'subject': 'e001',
        'object': 'e002', 'status': 'pending', 'verdict': 'insufficient', 'source': 'text',
        'evidence': '甲郎和乙郎一起到来。', 'scope': 'reality', 'hidden_from_reader': False}]
    ledger.save()
    return ledger


@pytest.mark.parametrize('merged,limit', [(False, 6), (True, 6), (True, 1)])
def test_indexed_pack_is_identical_to_old_disk_scan(tmp_path, merged, limit):
    ledger = make_ledger(tmp_path, merged)
    before = full_scan_evidence_pack(ledger, 'e001', 'e002', 'same_as', limit)
    index = ledger.entity_chapters()
    chapters = sorted(set(index['e001']) | set(index['e002']))
    after = resolve.evidence_pack(ledger, 'e001', 'e002', 'same_as', limit, chapters=chapters)
    assert after == before
    assert '老友' in after  # relation-only context outside either entity's mention index
    if merged and limit == 6:
        assert '甲郎旧称在第7章出现' in after


def test_cache_has_no_mention_or_relation_reads_after_load(tmp_path, monkeypatch):
    ledger = make_ledger(tmp_path, True)
    expected = full_scan_evidence_pack(ledger, 'e001', 'e002', 'same_as')
    def no_reads(*args, **kwargs):
        pytest.fail('evidence pack reread a production file')
    monkeypatch.setattr(store, 'read_json', no_reads)
    index = ledger.entity_chapters()
    assert resolve.evidence_pack(ledger, 'e001', 'e002', 'same_as',
        chapters=sorted(set(index['e001']) | set(index['e002']))) == expected


def test_index_tracks_multi_level_merges_and_undo(tmp_path):
    ledger = make_ledger(tmp_path, True)
    ledger.by_id['e003']['merged_into'] = 'e004'
    ledger.by_id['e004']['status'] = 'merged'
    ledger.by_id['e004']['merged_into'] = 'e001'
    assert ledger.entity_chapters()['e001'] == [1, 4, 7]
    ledger.by_id['e004'].update(status='active', merged_into=None)
    assert ledger.entity_chapters()['e001'] == [1]
    assert ledger.entity_chapters()['e004'] == [4, 7]


def test_resolve_refreshes_both_caches(tmp_path, monkeypatch):
    ledger = make_ledger(tmp_path)
    text = '甲郎走进房间。乙郎站在窗前。'
    raw = {'mentions': [{'form': name, 'entity': eid, 'kind': 'proper', 'presence': 'on_stage',
                         'evidence': line} for eid, name, line in (
        ('e001', '甲郎', '甲郎走进房间'), ('e002', '乙郎', '乙郎站在窗前'))],
        'new_entities': [], 'claims': [], 'relations': [{'from': 'e001', 'to': 'e002',
            'base': 'unknown', 'stance': 'neutral', 'address': '', 'hidden_from_reader': False,
            'evidence': '甲郎走进房间'}]}
    assert resolve.resolve_chapter(ledger, 9, text, raw, workers=1)['mentions'] == 2
    assert ledger.mentions_by_chapter[9] == store.read_json(ledger.mentions_path(9), [])
    assert ledger.relations_by_chapter[9] == store.read_json(ledger.relations_path(9), [])
    assert ledger.entity_chapters()['e001'] == [1, 9]
    assert resolve.evidence_pack(ledger, 'e001', 'e002', 'same_as') == full_scan_evidence_pack(ledger, 'e001', 'e002', 'same_as')


@pytest.mark.parametrize('failed_pass', [1, 2, 3])
def test_request_failure_never_becomes_a_rejection(tmp_path, monkeypatch, failed_pass):
    ledger = make_ledger(tmp_path)
    before = {p.name: p.read_bytes() for p in ledger.base.glob('*.json')}
    claims = copy.deepcopy(ledger.claims)
    def judge(pack, rules):
        from novel_manga.entities import contracts
        passes = {contracts.SETTLE_RULES: 1, contracts.SETTLE_RULES_B: 2, contracts.SETTLE_RULES_TIEBREAK: 3}
        if passes[rules] == failed_pass:
            return {'verdict': 'insufficient', 'why': 'error TimeoutError', 'error': True}
        return {'verdict': 'insufficient', 'why': '实际证据不足'}
    monkeypatch.setattr(ledger_judges, 'judge_settle', judge)
    with pytest.raises(RuntimeError, match='request failed'):
        resolve.settle_pending(ledger, workers=1)
    assert ledger.claims == claims
    assert {p.name: p.read_bytes() for p in ledger.base.glob('*.json')} == before
