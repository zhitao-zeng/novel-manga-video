from novel_manga.application.identity import ledger_store as store, ledger_resolution as resolution
from scripts.reconcile_cast_ledger import seed_candidates


def test_roster_membership_is_not_identity_proof(tmp_path):
    ledger = store.Ledger(tmp_path)
    raw = {'new_entities': [
        {'name': '小个子', 'description': '未确认描述'},
        {'name': '山羊', 'kind': 'animal', 'named': False, 'description': '灰山羊', 'evidence': '一只灰山羊冲了出来'},
        {'name': '林齐', 'kind': 'person', 'named': True, 'description': '青年', 'evidence': '林齐站在岸边'},
        {'name': '不存在', 'kind': 'person', 'named': True, 'evidence': '编造的句子'},
    ]}
    store.write_json(ledger.raw_path(1), raw)
    result = seed_candidates(ledger, ['小个子','山羊','林齐','不存在'], {1: '林齐站在岸边。一只灰山羊冲了出来。'})
    assert set(result) == {'山羊', '林齐'}
    assert result['山羊']['named'] is False and result['山羊']['kind'] == 'animal'


def test_missing_bilateral_endpoint_cannot_be_closed_as_one_sided(tmp_path):
    ledger = store.Ledger(tmp_path)
    ledger._add_entity('林齐', 'person', True, '', 1)
    ledger.claims = [{'id': 'same', 'type': 'same_as', 'subject': 'e001', 'object': 'UNCERTAIN', 'status': 'pending'},
                     {'id': 'reveal', 'type': 'reveal', 'subject': 'e001', 'object': 'UNCERTAIN', 'status': 'pending'},
                     {'id': 'death', 'type': 'death', 'subject': 'e001', 'object': '', 'status': 'pending'},
                     {'id': 'missing_subject', 'type': 'death', 'subject': 'UNCERTAIN', 'object': '', 'status': 'pending'}]
    result = resolution.close_leftovers(ledger)
    assert [r['id'] for r in result] == ['death']
    assert [r['id'] for r in ledger.claims if r['status'] == 'pending'] == ['same', 'reveal', 'missing_subject']
