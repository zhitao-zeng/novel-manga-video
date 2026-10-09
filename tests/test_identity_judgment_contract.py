import pytest
from novel_manga.application.identity import ledger_store, ledger_resolution, ledger_judges
from novel_manga.entities import evidence, contracts


@pytest.mark.parametrize('name', ['林齐', '云帝', '莲', '王小明', '姜子牙', '熊虎豹'])
def test_name_length_and_individual_characters_do_not_define_labels(name):
    assert evidence.generic_name(name) is False
    assert evidence.generic_record({'canonical': name, 'named': True, 'source': 'bible'}) is False


def test_confirmed_names_and_explicit_labels_keep_their_metadata():
    assert evidence.generic_record({'canonical': '黑斗篷男子', 'named': False, 'source': 'ch178'})
    assert evidence.generic_record({'canonical': '秘女', 'named': True, 'source': 'bible'})
    assert not evidence.generic_record({'canonical': '白猫', 'named': True,
                                       'source': 'ch1', 'evidence': '她正式取名白猫。'})


def test_main_character_name_and_card_survive_alias_merge(tmp_path):
    ledger_store.write_json(tmp_path / 'story_bible.json', {'characters': [{'name': '林齐', 'role': '主角'}]})
    ledger = ledger_store.Ledger(tmp_path)
    ledger.by_id['e001']['asset_id'] = 'character_001'
    alias = ledger._add_entity('熊虎豹', 'person', True, '', 583, '林齐化名的熊虎豹')
    ledger_resolution._merge(ledger, 'e001', alias['id'], 'c583', 583)
    assert ledger.canonical(alias['id']) == 'e001'
    assert ledger.by_id['e001']['canonical'] == '林齐'
    assert ledger.by_id['e001']['asset_id'] == 'character_001'


def test_tiebreak_receives_both_concrete_reasons(tmp_path, monkeypatch):
    ledger_store.write_json(tmp_path / 'story_bible.json', {'characters': [{'name': '甲角色'}, {'name': '乙角色'}]})
    ledger = ledger_store.Ledger(tmp_path)
    ledger.claims = [{'id': 'c1', 'chapter': 1, 'type': 'same_as', 'subject': 'e001', 'object': 'e002',
                     'status': 'pending', 'scope': 'reality', 'evidence': '甲乙同场。', 'hidden_from_reader': False}]
    seen = []
    def judge(pack, rules):
        if rules is contracts.SETTLE_RULES:
            return {'verdict': 'supports', 'why': '第一审认为师门继承是同人'}
        if rules is contracts.SETTLE_RULES_B:
            return {'verdict': 'contradicts', 'why': '第二审指出师傅和传人是两人'}
        seen.append(pack)
        return {'verdict': 'contradicts', 'why': '师徒非同人'}
    monkeypatch.setattr(ledger_judges, 'judge_settle', judge)
    ledger_resolution.settle_pending(ledger, workers=1)
    assert len(seen) == 1
    assert '第一审认为师门继承是同人' in seen[0]
    assert '第二审指出师傅和传人是两人' in seen[0]


def test_claim_request_carries_shared_type_meaning(monkeypatch):
    seen = []
    monkeypatch.setattr(ledger_judges, 'judge_settings', lambda: None)
    monkeypatch.setattr(ledger_judges, 'ask_json', lambda parts, *a, **kw: (
        seen.append(parts[0]['text']) or {'verdict': 'contradicts', 'why': '夫妻非同人'}))
    ledger_judges.judge_claim({'type': 'same_as', 'scope': 'reality', 'evidence': '姐姐嫁给皇子。'}, '姐姐', '皇子')
    assert contracts.CLAIM_MEANINGS['same_as'] in seen[0]
    assert contracts.IDENTITY_EVIDENCE_RULES in seen[0]
