import copy
import json

import pytest

from novel_manga.application.identity import semantic_review as review
from novel_manga.llm import client
from novel_manga.story.source_identity import clean_reading
from novel_manga.planning.text import split_segments


def actor(form='甲', paragraphs=None, *, sid=1, kind='individual', count=0):
    refs = [1] if paragraphs is None else paragraphs
    return {'source_id': sid, 'name': form, 'kind': kind, 'count': count,
            'presence': 'on_stage', 'appearance': '', 'paragraphs': refs,
            'forms': [{'form': form, 'kind': 'proper', 'paragraphs': refs}]}


@pytest.mark.parametrize('refs, texts, expected', [
    ([1, 2], ['甲来了。', '乙坐下。'], [1]),
    ([1, 3], ['乙坐下。', '甲来了。', '丙走了。'], [2]),
])
def test_each_form_reference_is_grounded_separately(refs, texts, expected):
    answer = {'actors': [actor(paragraphs=refs)]}
    before = copy.deepcopy(answer)
    cleaned, dropped, pending = clean_reading(answer, [{'text': t} for t in texts])

    form = cleaned['actors'][0]['forms'][0]
    assert form['paragraphs'] == expected
    assert form['cited'] == refs
    assert cleaned['actors'][0]['source_id'] == 1
    assert dropped == pending == []
    assert answer == before


def test_all_valid_references_and_normalized_names_are_kept():
    a = actor(form='甲乙', paragraphs=[1, 3])
    cleaned, dropped, pending = clean_reading({'actors': [a]}, [
        {'text': '甲\n乙来了。'}, {'text': '丙坐下。'}, {'text': '甲乙走了。'}])

    assert cleaned['actors'][0]['forms'] == a['forms']
    assert dropped == pending == []


def test_form_missing_from_the_whole_source_is_still_rejected():
    a = actor(form='原文没有的人', paragraphs=[1, 2])
    cleaned, dropped, pending = clean_reading({'actors': [a]}, [
        {'text': '甲来了。'}, {'text': '乙坐下。'}])

    assert cleaned['actors'] == []
    assert pending == [a] and dropped[0]['form'] == '原文没有的人'


def test_name_split_by_the_existing_segmenter_keeps_both_source_parts():
    segments = split_segments('齐\n夏走进屋里。', '标题', 8)
    a = actor('齐夏', paragraphs=[1, 2])
    cleaned, dropped, pending = clean_reading({'actors': [a]}, segments)

    assert cleaned['actors'] == [a]
    assert dropped == pending == []


def test_cross_segment_name_keeps_real_parts_and_drops_unrelated_reference():
    a = actor('齐夏', paragraphs=[1, 2, 3, 4])
    cleaned, dropped, pending = clean_reading({'actors': [a]}, [
        {'text': '齐'}, {'text': '夏走进屋里。'},
        {'text': '别人在门外。'}, {'text': '齐夏坐下。'}])

    assert cleaned['actors'][0]['forms'][0]['paragraphs'] == [1, 2, 4]
    assert cleaned['actors'][0]['forms'][0]['cited'] == [1, 2, 3, 4]
    assert dropped == pending == []


def test_name_parts_cannot_be_joined_over_an_unrelated_segment():
    a = actor('齐夏', paragraphs=[1, 3])
    cleaned, dropped, pending = clean_reading({'actors': [a]}, [
        {'text': '齐'}, {'text': '别人在门外。'}, {'text': '夏走进屋里。'}])

    assert cleaned['actors'] == []
    assert pending == [a] and dropped[0]['form'] == '齐夏'


def prompt_payload(parts):
    text = parts[0]['text']
    start = text.rfind('\n{"evidence":') + 1
    assert start > 0
    return json.JSONDecoder().raw_decode(text[start:])[0]


def candidate():
    actors = [actor(), actor('同行者', sid=2, kind='group'),
              actor('布袋', sid=3, kind='object'), actor('乙', sid=4, count=1)]
    return {'actors': actors, 'bindings': [
        {'source_id': row['source_id'], 'entity_id': 'UNKNOWN'} for row in actors]}


def test_single_count_is_normalized_only_in_review_input(monkeypatch, tmp_path):
    original = candidate()
    before = copy.deepcopy(original)
    calls = []
    def ask(parts, *args, **kwargs):
        seen = prompt_payload(parts)['candidate']
        assert [a['count'] for a in seen['actors']] == [1, 0, 0, 1]
        assert seen['bindings'] == before['bindings']
        seen['actors'][0]['name'] = '改动视图不应污染原记录'
        calls.append(kwargs['name'])
        return {'approved': True, 'reason': '当前候选合格', 'correction': None}
    monkeypatch.setattr(client, 'ask_json', ask)
    path = tmp_path / 'review.json'
    value, receipt = review.close(original, {}, {}, name='test_review', record=path)

    assert value == original == before
    assert receipt['inputs']['candidate'] == receipt['result'] == before
    trace = json.loads(path.read_text())
    assert trace['inputs']['candidate'] == trace['steps'][0]['candidate'] == before
    assert calls == ['test_review']


def test_normalization_does_not_reopen_an_approved_cached_result(monkeypatch):
    original = candidate()
    cache = {'approved': True, 'inputs': {'context': {}, 'candidate': original},
             'result': copy.deepcopy(original)}
    if hasattr(review, 'REVIEW_POLICY'):
        cache['policy'] = review.REVIEW_POLICY
    before = copy.deepcopy(cache)
    def forbidden(*args, **kwargs):
        pytest.fail('The approved unchanged cache must be reused')
    monkeypatch.setattr(client, 'ask_json', forbidden)

    value, receipt = review.close(original, {}, {}, name='test_review', cached=cache)

    assert value == original and receipt == cache == before
    assert value['actors'][0]['count'] == 0


def test_count_projection_does_not_override_formal_refusal(monkeypatch):
    original = candidate()
    before = copy.deepcopy(original)
    calls = []
    def ask(parts, *args, **kwargs):
        assert prompt_payload(parts)['candidate']['actors'][0]['count'] == 1
        calls.append(kwargs['name'])
        return {'approved': False, 'reason': '还有真实身份错误', 'correction': None}
    monkeypatch.setattr(client, 'ask_json', ask)

    with pytest.raises(ValueError, match='identity semantic review unresolved'):
        review.close(original, {}, {}, name='test_review')

    assert len(calls) == 2 and original == before


def test_count_projection_still_rejects_approved_unreviewed_changes(monkeypatch):
    original = candidate()
    before = copy.deepcopy(original)
    def ask(parts, *args, **kwargs):
        seen = prompt_payload(parts)['candidate']
        assert seen['actors'][0]['count'] == 1
        return {'approved': True, 'reason': '已修改', 'correction': seen}
    monkeypatch.setattr(client, 'ask_json', ask)

    with pytest.raises(ValueError, match='approved a different, unreviewed correction'):
        review.close(original, {}, {}, name='test_review')

    assert original == before
