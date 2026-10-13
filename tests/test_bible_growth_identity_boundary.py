"""Card design suggestions must not adopt or overwrite book identity decisions."""
import json

import pytest

from novel_manga.application.identity import bible_growth, ledger_store
from novel_manga.llm import client
from novel_manga.models.bible import Character, StoryBible
from novel_manga.util import atomic_write_json


def prepare_book(book):
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='2d',
                       palette='蓝', style_fingerprint='test', locations=[],
                       characters=[Character(name='南宫问天', appearance='青年', wardrobe='白衣')])
    (book / 'story_bible.json').write_text(bible.model_dump_json(), encoding='utf-8')
    atomic_write_json(book / 'bible_growth.json', {
        '1': {'characters': [], 'locations': [], 'suggestions': {},
              'needs_human': {'旧称呼': '历史待定判断'}}})


def freeze_design(monkeypatch):
    answer = {'name': '陌生客', 'same_as': '南宫问天', 'confidence': 1,
              'appearance': '青年', 'wardrobe': '白衣'}
    calls = []

    def ask(*args, **kwargs):
        assert kwargs['name'] == 'fill'
        calls.append(kwargs['name'])
        return {'characters': [answer]}

    monkeypatch.setattr(client, 'ask_json', ask)
    return calls


def grow(book, chapter=2):
    return bible_growth.grow_bible(book, '陌生客向南宫问天点头。', chapter, scan={
        'names': [{'name': '陌生客', 'kind': '具名角色', 'mentions': 3,
                   'speaks_or_close_up': True}], 'locations': []})


@pytest.mark.parametrize('decision', ['accepted', 'rejected', 'pending'])
def test_design_candidate_preserves_identity_history_and_design_links(tmp_path, monkeypatch, decision):
    prepare_book(tmp_path)
    # Keep historical non-positional IDs and both old design references, even for a merged record.
    atomic_write_json(tmp_path / 'entity/entities.json', [
        {'id': 'e007', 'canonical': '南宫问天', 'kind': 'person', 'status': 'active',
         'named': True, 'asset_id': 'char03', 'source': 'bible', 'merged_into': None},
        {'id': 'e019', 'canonical': '陌生客', 'kind': 'person', 'named': True,
         'status': 'merged' if decision == 'accepted' else 'active',
         'asset_id': 'char08', 'source': 'ch1',
         'merged_into': 'e007' if decision == 'accepted' else None}])
    claim = {'id': 'c0001-01', 'chapter': 1, 'type': 'same_as', 'subject': 'e019',
             'object': 'e007', 'status': decision, 'evidence': '历史原文', 'span': [0, 4]}
    atomic_write_json(tmp_path / 'entity/claims.json', [claim])
    atomic_write_json(tmp_path / 'entity/corrections.json', [] if decision == 'pending' else [{
        'claim': claim['id'], 'type': 'same_as', 'subject': 'e019', 'object': 'e007',
        'decision': decision, 'by': 'human', 'note': '保留历史判断'}])
    atomic_write_json(tmp_path / 'series_assets/manifest.json', {'characters': [
        {'name': '南宫问天', 'asset_id': 'char03'}, {'name': '陌生客', 'asset_id': 'char08'}]})
    atomic_write_json(tmp_path / 'series_assets/character_phases.json', {'char03': {'wardrobe': '旧衣'}})
    ledger = ledger_store.Ledger(tmp_path)
    ledger.save()
    original_canonical = ledger.canonical('e019')
    # The old alias is still reusable; design has no permission to rewrite even this file.
    aliases = tmp_path / 'bible_aliases.json'
    aliases.write_text('{"问天": "南宫问天"}\n', encoding='utf-8')
    aliases_mtime = aliases.stat().st_mtime_ns
    protected = [tmp_path / 'story_bible.json', aliases, tmp_path / 'entity_index.json',
                 *sorted((tmp_path / 'entity').rglob('*.json')),
                 *sorted((tmp_path / 'series_assets').rglob('*.json'))]
    before = {path: path.read_bytes() for path in protected}
    history = json.loads((tmp_path / 'bible_growth.json').read_text())['1']
    calls = freeze_design(monkeypatch)

    result = grow(tmp_path)

    assert {path: path.read_bytes() for path in protected} == before
    assert aliases.stat().st_mtime_ns == aliases_mtime
    assert ledger_store.Ledger(tmp_path).canonical('e019') == original_canonical
    saved = json.loads((tmp_path / 'bible_growth.json').read_text())
    assert saved['1'] == history
    assert result['characters'] == []
    assert saved['2']['suggestions'] == result['suggestions']
    assert saved['2']['suggestions']['陌生客']['same_as'] == '南宫问天'
    assert saved['2']['needs_human']['陌生客'] == '疑为 南宫问天 的别称'
    # A held suggestion remains held on the next chapter; it does not trigger another design call.
    grow(tmp_path, chapter=3)
    assert calls == ['fill']
    assert json.loads((tmp_path / 'bible_growth.json').read_text())['2'] == saved['2']


def test_design_candidate_does_not_create_aliases_or_permanent_identities(tmp_path, monkeypatch):
    prepare_book(tmp_path)
    before = (tmp_path / 'story_bible.json').read_bytes()
    calls = freeze_design(monkeypatch)

    result = grow(tmp_path)

    assert not (tmp_path / 'bible_aliases.json').exists()
    assert not (tmp_path / 'entity').exists()
    assert (tmp_path / 'story_bible.json').read_bytes() == before
    assert result['suggestions']['陌生客']['same_as'] == '南宫问天'
    assert calls == ['fill']
