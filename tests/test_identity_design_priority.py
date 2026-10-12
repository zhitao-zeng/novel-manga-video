"""Artwork/plot prominence must not choose an identity for an ambiguous actor."""
from copy import deepcopy

import pytest

from novel_manga.story.catalog import IdentityCatalog
from novel_manga.story.source_identity import map_source_reading


def catalog(lead):
    records = [{'id': 'e001', 'canonical': '林甲', 'kind': 'person', 'named': True, 'status': 'active'},
               {'id': 'e002', 'canonical': '李乙', 'kind': 'person', 'named': True, 'status': 'active'}]
    bible = {'characters': [{'name': '林甲', 'role': lead}, {'name': '李乙', 'role': '配角'}]}
    return IdentityCatalog(bible, records, {}, {}, [], {})


def actor(name, forms, kind='individual'):
    return {'source_id': 1, 'name': name, 'kind': kind, 'count': 2 if kind == 'group' else 1,
            'presence': 'on_stage', 'appearance': '', 'paragraphs': [1],
            'forms': [{'form': form, 'kind': 'proper', 'paragraphs': [1]} for form in forms]}


@pytest.mark.parametrize('lead', ['主角', '男主角', '女主角', '配角'])
def test_multiple_named_candidates_do_not_select_the_lead_design(lead):
    answer = {'actors': [actor('同行者', ['林甲', '李乙'], 'group')]}
    before = deepcopy(answer)
    result = map_source_reading(answer, catalog(lead), [{'text': '林甲和李乙站在门口。'}])
    assert {r['entity_id'] for r in result['mentions']} == {'UNKNOWN'}
    assert result['source_actors'] == before['actors'] and answer == before


def test_unique_name_still_selects_existing_identity():
    result = map_source_reading({'actors': [actor('客人', ['李乙'])]}, catalog('主角'),
                               [{'text': '李乙走来。'}])
    assert {r['entity_id'] for r in result['mentions']} == {'e002'}


def test_verified_aliases_of_one_person_still_resolve():
    result = map_source_reading({'actors': [actor('来客', ['来客', '客人'])]}, catalog('主角'),
        [{'text': '来客坐下，客人随后笑了。'}], {'来客': '李乙', '客人': '李乙'})
    assert {r['entity_id'] for r in result['mentions']} == {'e002'}


def test_explicitly_approved_binding_is_not_rejudged_by_candidate_lookup():
    result = map_source_reading({'actors': [actor('同行者', ['林甲', '李乙'], 'group')]}, catalog('主角'),
        [{'text': '林甲和李乙站在门口。'}], approved_bindings={1: 'e002'})
    assert {r['entity_id'] for r in result['mentions']} == {'e002'}
