"""Rejected identity proposals never become the base for protocol correction."""
from copy import deepcopy
import json

import pytest

from novel_manga.application.identity import semantic_review as review
from novel_manga.story.catalog import IdentityCatalog
from novel_manga.story.source_identity import map_source_reading


REAL_BINDINGS = review.bindings


def actor(sid, name, paragraphs):
    return {'source_id': sid, 'name': name, 'kind': 'individual', 'count': 1,
            'presence': 'on_stage', 'appearance': '', 'paragraphs': paragraphs,
            'forms': [{'form': name, 'kind': 'proper', 'paragraphs': paragraphs}]}


def binding(sid, eid, paragraphs):
    return {'source_id': sid, 'entity_id': eid, 'entity_kind': None,
            'decision': 'catalogue' if eid != 'UNKNOWN' else 'extra',
            'paragraphs': paragraphs, 'reason': ''}


def inputs():
    records = [{'id': 'e071', 'canonical': '甲', 'asset_id': 'character_023'},
               {'id': 'e099', 'canonical': '旧甲', 'merged_into': 'e071'},
               {'id': 'e072', 'canonical': '乙', 'asset_id': 'character_024'}]
    bible = {'characters': [{'name': '甲', 'appearance': '原设计甲'},
                             {'name': '乙', 'appearance': '原设计乙'}]}
    history = [{'type': 'same_as', 'subject': 'e099', 'object': 'e071', 'status': 'accepted'}]
    catalog = IdentityCatalog(bible, records, {}, {}, history, {})
    segments = [{'segment_id': 's1', 'text': '甲走进屋里。'},
                {'segment_id': 's2', 'text': '甲看向乙。'}]
    reading = {'actors': [actor(1, '甲', [1]), actor(2, '乙', [2])]}
    corrected = actor(1, '甲', [1, 2])
    invented = {**actor(3, '背景名字', [2]), 'forms': []}
    proposal = {'actors': [corrected, invented],
                'bindings': [binding(1, 'e071', [1, 2]), binding(3, 'UNKNOWN', [2])]}
    repaired = {'actors': [corrected], 'bindings': [binding(1, 'e071', [1, 2])]}
    return catalog, segments, reading, proposal, repaired


@pytest.mark.parametrize('final_approved', [True, False])
@pytest.mark.parametrize('include_unchanged_rows', [False, True])
def test_repaired_delta_replaces_rejected_delta_and_keeps_original_subjects(
        tmp_path, monkeypatch, final_approved, include_unchanged_rows):
    catalog, segments, reading, proposal, repaired = inputs()
    if include_unchanged_rows:
        repaired['actors'].append(deepcopy(reading['actors'][1]))
        repaired['bindings'].append({**binding(2, 'e072', [2]), 'reason': '主体字段未变'})
    before = deepcopy((reading, catalog.entities, catalog.records, catalog.claims))
    answers = [{'approved': False, 'reason': '补位置', 'correction': proposal}, repaired,
               {'approved': final_approved, 'reason': '最终核对',
                'correction': None if final_approved else repaired}]
    calls = []

    def ask(parts, *args, **kwargs):
        name = kwargs['name']; calls.append(name)
        payload = json.loads(parts[0]['text'].split('\n', 1)[1])
        if name.endswith('_correction'):
            assert payload['previous_candidate']['actors'] == reading['actors']
            assert payload['rejected_proposal'] == proposal
        elif len(calls) == 3:
            assert {a['source_id'] for a in payload['candidate']['actors']} == {1, 2}
            assert '背景名字' not in json.dumps(payload['candidate'], ensure_ascii=False)
        return deepcopy(answers[len(calls) - 1])

    monkeypatch.setattr(review.client, 'ask_json', ask)
    mapped = map_source_reading(reading, catalog, segments)
    episode = tmp_path / 'book/book_48'
    if final_approved:
        value, receipt = REAL_BINDINGS(episode, reading, catalog, segments, mapped)
        assert receipt['approved']
        assert value['actors'] == [repaired['actors'][0], reading['actors'][1]]
        assert {b['source_id']: b['entity_id'] for b in value['bindings']} == {1: 'e071', 2: 'e072'}
    else:
        with pytest.raises(ValueError, match='identity semantic review unresolved'):
            REAL_BINDINGS(episode, reading, catalog, segments, mapped)
    assert calls == ['chapter_binding_review', 'chapter_binding_review_correction', 'chapter_binding_review']
    assert (reading, catalog.entities, catalog.records, catalog.claims) == before


@pytest.mark.parametrize('field', ['actors', 'bindings', 'removed_source_ids'])
def test_protocol_correction_cannot_change_an_unrelated_original_subject(tmp_path, monkeypatch, field):
    catalog, segments, reading, proposal, repaired = inputs()
    if field == 'actors':
        repaired['actors'].append({**deepcopy(reading['actors'][1]), 'presence': 'mentioned'})
    elif field == 'bindings':
        repaired['bindings'].append(binding(2, 'UNKNOWN', [2]))
    else:
        repaired['removed_source_ids'] = [2]
    before = deepcopy((reading, catalog.entities, catalog.records, catalog.claims))
    answers = [{'approved': False, 'reason': '补位置', 'correction': proposal}, repaired]
    calls = []

    def ask(parts, *args, **kwargs):
        calls.append(kwargs['name'])
        assert len(calls) <= 2
        return deepcopy(answers[len(calls) - 1])

    monkeypatch.setattr(review.client, 'ask_json', ask)
    with pytest.raises(ValueError, match='protocol correction changes unrelated source actors.*2'):
        REAL_BINDINGS(tmp_path / 'book/book_48', reading, catalog, segments,
                      map_source_reading(reading, catalog, segments))
    assert calls == ['chapter_binding_review', 'chapter_binding_review_correction']
    assert (reading, catalog.entities, catalog.records, catalog.claims) == before


def test_repair_with_still_ungrounded_new_subject_still_fails(tmp_path, monkeypatch):
    catalog, segments, reading, proposal, _ = inputs()
    answers = [{'approved': False, 'reason': '补位置', 'correction': proposal}, proposal]
    calls = []

    def ask(parts, *args, **kwargs):
        calls.append(kwargs['name'])
        assert len(calls) <= 2
        return deepcopy(answers[len(calls) - 1])

    monkeypatch.setattr(review.client, 'ask_json', ask)
    with pytest.raises(ValueError, match='ungrounded source forms.*背景名字'):
        REAL_BINDINGS(tmp_path / 'book/book_48', reading, catalog, segments,
                      map_source_reading(reading, catalog, segments))
    assert calls == ['chapter_binding_review', 'chapter_binding_review_correction']
