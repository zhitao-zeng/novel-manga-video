"""A failed binding review must not discard an independent source extraction."""
from copy import deepcopy
import json

import pytest

from novel_manga.application.identity import flow, ledger_store, semantic_review
from novel_manga.llm import client
from novel_manga.story.catalog import IdentityCatalog
from novel_manga.story.source_identity import map_source_reading

REAL_BINDINGS = semantic_review.bindings


@pytest.fixture(autouse=True)
def production_review(monkeypatch, isolated_identity_semantic_service):
    monkeypatch.setattr(semantic_review, 'bindings', REAL_BINDINGS)


def actor(name='甲'):
    return {'source_id': 1, 'name': name, 'kind': 'individual', 'count': 1,
            'presence': 'on_stage', 'appearance': '', 'paragraphs': [1],
            'forms': [{'form': name, 'kind': 'proper', 'paragraphs': [1]}]}


def episode(tmp_path):
    book = tmp_path/'book'
    ledger_store.write_json(book/'story_bible.json',
                            {'characters': [{'name': '甲', 'role': '人物'}], 'locations': []})
    ledger_store.Ledger(book).save()
    path = book/'book_1'
    ledger_store.write_json(path/'segments.json', [{'text': '甲走进屋里。'}])
    return path


def test_interrupted_binding_reuses_source_but_requires_a_new_valid_review(tmp_path, monkeypatch):
    path = episode(tmp_path)
    calls = []
    approved = False

    def ask(*args, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'chapter_identity_source':
            return {'source_readable': True, 'source_problem': '', 'actors': [actor()]}
        if not approved:
            raise TimeoutError('身份审查没有交付有效判定')
        return {'approved': True, 'reason': '原文支持', 'correction': None}

    monkeypatch.setattr(client, 'ask_json', ask)
    with pytest.raises(TimeoutError, match='没有交付有效判定'):
        flow.resolve_chapter(path)
    assert (path/'identity_source_reading.json').is_file()
    assert not (path/'identity_context.json').exists()
    approved = True
    result = flow.resolve_chapter(path)
    assert calls.count('chapter_identity_source') == 1
    assert calls.count('chapter_binding_review') == 2
    assert result['semantic_review']['approved']
    assert result['source_actors'][0]['name'] == '甲'


@pytest.mark.parametrize('change', ['source', 'force', 'empty', 'unreadable'])
def test_source_changes_or_unfinished_cache_still_require_extraction(tmp_path, monkeypatch, change):
    path = episode(tmp_path)
    from novel_manga.application.identity.store import chapter_inputs
    answer = {'source_readable': True, 'source_problem': '', 'actors': [actor()]}
    if change == 'empty':
        answer['actors'] = []
    if change == 'unreadable':
        answer['source_readable'] = False
    ledger_store.write_json(path/'identity_source_reading.json',
        {'policy': flow.POLICY, 'inputs': chapter_inputs(path), 'answer': answer})
    if change == 'source':
        ledger_store.write_json(path/'segments.json', [{'text': '甲走出屋外。'}])
    calls = []

    def ask(*args, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'chapter_identity_source':
            return {'source_readable': True, 'source_problem': '', 'actors': [actor()]}
        return {'approved': True, 'reason': '当前原文支持', 'correction': None}

    monkeypatch.setattr(client, 'ask_json', ask)
    result = flow.resolve_chapter(path, force=change == 'force')
    assert calls[0] == 'chapter_identity_source'
    assert result['semantic_review']['approved']


def test_review_keeps_lookup_names_and_aliases_without_unrelated_forms(tmp_path, monkeypatch):
    bible = {'characters': [{'name': '甲'}, {'name': '无关人物'}], 'locations': []}
    catalog = IdentityCatalog(bible, [], {'小甲': '甲', '远方称谓': '无关人物'}, {}, [], {})
    segments = [{'text': '小甲走进屋里。'}]
    reading = {'actors': [actor('小甲')]}
    mapped = map_source_reading(reading, catalog, segments, {'小甲': '甲'})
    before = deepcopy(catalog.entities)

    def ask(parts, *args, **kwargs):
        payload = json.loads(parts[0]['text'].split('\n')[-1])
        rows = {r['name']: r for r in payload['evidence']['catalogue']}
        assert set(rows) == {'甲'}
        assert '小甲' in rows['甲']['forms']
        return {'approved': True, 'reason': '原文称呼与在册正名同指', 'correction': None}

    monkeypatch.setattr(client, 'ask_json', ask)
    value, receipt = semantic_review.bindings(tmp_path/'book_1', reading, catalog,
        segments, mapped, aliases={'小甲': '甲'})
    assert receipt['approved']
    assert value['actors'][0]['name'] == '小甲'
    assert value['bindings'][0]['entity_id'] != 'UNKNOWN'
    assert catalog.entities == before


@pytest.mark.parametrize('approved', ['false', 'true', 1, None])
def test_review_rejects_a_non_boolean_judgment(monkeypatch, approved):
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k:
                        {'approved': approved, 'reason': '看似支持', 'correction': None})
    with pytest.raises(ValueError, match='no complete typed judgment'):
        semantic_review.close({}, {}, {}, name='review')


def test_explanatory_text_cannot_override_an_explicit_failure(monkeypatch):
    calls = []
    def ask(*args, **kwargs):
        calls.append(kwargs['name'])
        return {'approved': False, 'reason': '文字称approved=true不构成正式通过', 'correction': None}
    monkeypatch.setattr(client, 'ask_json', ask)
    with pytest.raises(ValueError, match='identity semantic review unresolved'):
        semantic_review.close({}, {}, {}, name='review')
    assert calls == ['review', 'review']


@pytest.mark.parametrize('stale,invalid', [(False, False), (True, False), (False, True)])
def test_normal_retry_continues_only_a_changed_valid_pending_candidate(tmp_path, monkeypatch, stale, invalid):
    candidate = {'value': 0}
    answers = iter([
        {'approved': False, 'reason': '第一次修订', 'correction': {'value': 1}},
        {'approved': False, 'reason': '尚需修订', 'correction': {'value': -1 if invalid else 2}},
        {'approved': True, 'reason': '当前稿有效', 'correction': None}])
    seen = []
    def ask(parts, *args, **kwargs):
        seen.append(json.loads(parts[0]['text'].split('\n')[-1])['candidate'])
        return next(answers)
    def validate(value):
        if value['value'] < 0:
            raise ValueError('无效候选')
        return value
    monkeypatch.setattr(client, 'ask_json', ask)
    path = tmp_path/'review.json'
    with pytest.raises(ValueError, match='尚需修订'):
        semantic_review.close(candidate, {'version': 1}, {}, name='review', validate=validate, record=path)
    pending = json.loads(path.read_text())
    if invalid and not stale:
        with pytest.raises(ValueError, match='尚需修订'):
            semantic_review.close(candidate, {'version': 1}, {}, name='review',
                                  validate=validate, pending=pending, record=path)
        assert seen == [{'value': 0}, {'value': 1}]
        assert not pending['approved']
        return
    result, receipt = semantic_review.close(candidate, {'version': 2 if stale else 1}, {},
        name='review', validate=validate, pending=pending, record=path)
    expected = 0 if stale or invalid else 2
    assert seen == [{'value': 0}, {'value': 1}, {'value': expected}]
    assert result == {'value': expected} and receipt['approved']
    assert candidate == {'value': 0}
    assert json.loads(path.with_suffix('.history.jsonl').read_text()) == pending


def test_unrelated_new_character_does_not_repeat_an_approved_chapter_review(tmp_path, monkeypatch):
    path = episode(tmp_path)
    calls = []
    def ask(*args, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'chapter_identity_source':
            return {'source_readable': True, 'source_problem': '', 'actors': [actor()]}
        return {'approved': True, 'reason': '当前人物归属正确', 'correction': None}
    monkeypatch.setattr(client, 'ask_json', ask)
    original = flow.resolve_chapter(path)
    bible_path = path.parent/'story_bible.json'
    bible = json.loads(bible_path.read_text())
    bible['characters'].append({'name': '远方无关人物', 'role': '人物'})
    ledger_store.write_json(bible_path, bible)
    result = flow.resolve_chapter(path)
    assert calls == ['chapter_identity_source', 'chapter_binding_review']
    assert result['mentions'] == original['mentions']
    assert result['semantic_review']['approved'] is True


def test_unchanged_failed_review_is_not_rerolled_by_a_batch_retry(tmp_path, monkeypatch):
    path = episode(tmp_path)
    calls = []
    def ask(*args, **kwargs):
        calls.append(kwargs['name'])
        if kwargs['name'] == 'chapter_identity_source':
            return {'source_readable': True, 'source_problem': '', 'actors': [actor()]}
        return {'approved': False, 'reason': '身份证据不足', 'correction': None}
    monkeypatch.setattr(client, 'ask_json', ask)
    with pytest.raises(ValueError, match='身份证据不足'):
        flow.resolve_chapter(path)
    first_calls = list(calls)
    with pytest.raises(ValueError, match='身份证据不足'):
        flow.resolve_chapter(path)
    assert calls == first_calls
    assert not (path/'identity_context.json').exists()


def test_approved_receipt_survives_interruption_before_context_publication(tmp_path, monkeypatch):
    candidate = {'value': 1}
    pending = {'inputs': {'context': {'chapter': 1}, 'candidate': candidate},
               'approved': True, 'result': candidate, 'steps': []}
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: pytest.fail('already reviewed'))
    result, receipt = semantic_review.close(candidate, {'chapter': 1}, {}, name='review', pending=pending)
    assert result == candidate and receipt['approved'] is True


def test_review_history_is_one_complete_receipt_per_line(tmp_path, monkeypatch):
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k:
                        {'approved': True, 'reason': '有效', 'correction': None})
    path = tmp_path/'review.json'
    for version in [1, 2, 3]:
        semantic_review.close({}, {'version': version}, {}, name='review', record=path)
    rows = [json.loads(line) for line in path.with_suffix('.history.jsonl').read_text().splitlines()]
    assert [row['inputs']['context']['version'] for row in rows] == [1, 2]


def test_new_relevant_alias_reopens_binding_review(tmp_path, monkeypatch):
    bible = {'characters': [{'name': '甲'}], 'locations': []}
    segments = [{'text': '小甲进门。'}]
    reading = {'actors': [actor('小甲')]}
    calls = []
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: calls.append(k['name']) or
                        {'approved': True, 'reason': '当前证据支持', 'correction': None})
    catalog = IdentityCatalog(bible, [], {}, {}, [], {})
    _, cached = semantic_review.bindings(tmp_path/'book_1', reading, catalog, segments,
                                        map_source_reading(reading, catalog, segments))
    catalog = IdentityCatalog(bible, [], {'小甲': '甲'}, {}, [], {})
    value, receipt = semantic_review.bindings(tmp_path/'book_1', reading, catalog, segments,
        map_source_reading(reading, catalog, segments, {'小甲': '甲'}),
        aliases={'小甲': '甲'}, cached=cached)
    assert calls == ['chapter_binding_review', 'chapter_binding_review']
    assert value['bindings'][0]['entity_id'] != 'UNKNOWN' and receipt['approved']


def test_previous_global_name_menu_cache_reuses_only_unchanged_related_evidence(monkeypatch):
    related = {'id': 'e001', 'name': '甲', 'forms': ['甲']}
    context = {'catalogue': [related]}
    old_context = {'catalogue': [related, {'id': 'e002', 'name': '远方人物', 'forms': []}]}
    candidate = {'value': 1}
    saved = {'approved': True, 'inputs': {'context': old_context, 'candidate': candidate}, 'result': candidate}
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: pytest.fail('unrelated index is not new evidence'))
    result, receipt = semantic_review.close(candidate, context, {}, name='review', cached=saved)
    assert result == candidate and receipt['approved']
    assert receipt['inputs']['context'] == context


def test_repeated_transport_interruptions_keep_the_valid_pending_revision(tmp_path, monkeypatch):
    path = tmp_path/'review.json'
    seen = []
    failures = [False, True, True, False]
    def ask(parts, *args, **kwargs):
        value = json.loads(parts[0]['text'].split('\n')[-1])['candidate']
        seen.append(value)
        if failures.pop(0):
            raise TimeoutError('response interrupted')
        return {'approved': value['value'] == 1, 'reason': '改为有依据的候选',
                'correction': {'value': 1} if value['value'] == 0 else None}
    monkeypatch.setattr(client, 'ask_json', ask)
    for _ in range(2):
        pending = json.loads(path.read_text()) if path.exists() else None
        with pytest.raises(TimeoutError, match='interrupted'):
            semantic_review.close({'value': 0}, {}, {}, name='review', record=path, pending=pending)
    result, receipt = semantic_review.close({'value': 0}, {}, {}, name='review', record=path,
                                           pending=json.loads(path.read_text()))
    assert seen == [{'value': 0}, {'value': 1}, {'value': 1}, {'value': 1}]
    assert result == {'value': 1} and receipt['approved']
