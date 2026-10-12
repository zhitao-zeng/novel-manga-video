import copy

import pytest

from novel_manga.story.catalog import IdentityCatalog


def merged_records(asset_id=None):
    return [
        {'id': 'e034', 'canonical': '微胖女生', 'named': False,
         'status': 'active', 'merged_into': None, 'asset_id': asset_id},
        {'id': 'e038', 'canonical': '潇潇', 'named': True,
         'status': 'merged', 'merged_into': 'e034', 'asset_id': 'character_037'},
    ]


@pytest.mark.parametrize('asset_id', [None, 'character_existing'])
def test_merged_owner_name_keeps_the_existing_identity_and_design(asset_id):
    bible = {'characters': [{'name': '潇潇', 'role': '人物', 'appearance': '原设计'}]}
    ledger = merged_records(asset_id)
    claims = [{'subject': 'e038', 'object': 'e034', 'status': 'accepted'}]
    phases = {'characters': {'潇潇': {'asset_id': 'phase_design_existing'}}}
    before = copy.deepcopy((bible, ledger, claims, phases))

    catalog = IdentityCatalog(bible, ledger, {}, {}, claims, phases)

    assert catalog.by_name['微胖女生'] is catalog.by_name['潇潇']
    owner = catalog.by_name['微胖女生']
    assert owner['id'] == 'e034'
    assert owner['forms'] == {'微胖女生', '潇潇'}
    assert owner['name'] == '潇潇'
    assert owner['design'] == bible['characters'][0]
    assert owner['asset_id'] == (asset_id or 'character_001')
    assert set(catalog.entities) == {'e034', 'voice:collective'}
    assert catalog.records == ledger and catalog.claims == claims
    assert catalog.phases == phases['characters']
    assert (bible, ledger, claims, phases) == before


def test_merged_name_chain_keeps_all_existing_names_on_one_owner():
    ledger = merged_records()
    ledger.append({'id': 'e039', 'canonical': '潇潇妹妹', 'named': True,
                   'status': 'merged', 'merged_into': 'e038', 'asset_id': None})
    before = copy.deepcopy(ledger)
    catalog = IdentityCatalog({'characters': [{'name': '潇潇妹妹'}]}, ledger,
                              {}, {}, [], {})

    assert {catalog.by_name[name]['id'] for name in
            ('微胖女生', '潇潇', '潇潇妹妹')} == {'e034'}
    assert catalog.entities['e034']['forms'] == {'微胖女生', '潇潇', '潇潇妹妹'}
    assert catalog.records == ledger == before
    assert set(catalog.entities) == {'e034', 'voice:collective'}


def test_unmerged_unnamed_record_without_design_is_not_newly_bound():
    ledger = merged_records()[:1]
    catalog = IdentityCatalog({'characters': []}, ledger, {}, {}, [], {})

    assert '微胖女生' not in catalog.by_name
    assert 'e034' not in catalog.entities
    assert catalog.records == ledger
