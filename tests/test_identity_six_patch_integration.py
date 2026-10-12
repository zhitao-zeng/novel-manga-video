"""Exercise overlapping small fixes together, with frozen model replies only."""
from copy import deepcopy
import json

from novel_manga.application.identity import semantic_review as review
from novel_manga.llm import client
from novel_manga.story.catalog import IdentityCatalog
from novel_manga.story.source_identity import clean_reading, map_source_reading


REAL_BINDINGS = review.bindings


def test_six_patches_preserve_data_and_review_the_repaired_candidate(tmp_path, monkeypatch):
    records = [
        {'id': 'e034', 'canonical': '微胖女生'},
        {'id': 'e038', 'canonical': '潇潇', 'merged_into': 'e034'},
        {'id': 'e001', 'canonical': '林甲'}, {'id': 'e002', 'canonical': '李乙'}]
    bible = {'characters': [{'name': '潇潇'}, {'name': '林甲', 'role': '主角'},
                             {'name': '李乙', 'role': '配角'}]}
    history = [{'subject': 'e038', 'object': 'e034', 'status': 'accepted'}]
    segments = [{'segment_id': 's1', 'text': '微胖女生说：“走吧。”'},
                {'segment_id': 's2', 'text': '林甲与李乙走出门口。'}]
    raw = {'actors': [
        {'source_id': 1, 'name': '微胖女生', 'kind': 'individual', 'count': 0,
         'presence': 'mentioned', 'appearance': '', 'paragraphs': [1],
         'forms': [{'form': '微胖女生', 'kind': 'contextual', 'paragraphs': [1, 2]}]},
        {'source_id': 2, 'name': '同行者', 'kind': 'group', 'count': 2,
         'presence': 'on_stage', 'appearance': '', 'paragraphs': [2],
         'forms': [{'form': n, 'kind': 'proper', 'paragraphs': [2]} for n in ['林甲', '李乙']]}]}
    before = deepcopy((records, bible, history, segments, raw))
    catalog = IdentityCatalog(bible, records, {}, {}, history, {})
    cleaned, rejected, unresolved = clean_reading(raw, segments)
    assert rejected == unresolved == []
    assert cleaned['actors'][0]['forms'][0]['paragraphs'] == [1]
    mapped = map_source_reading(cleaned, catalog, segments)
    assert {m['source_actor']: m['entity_id'] for m in mapped['mentions']} == {1: 'e034', 2: 'UNKNOWN'}
    correction = deepcopy(cleaned['actors'][0])
    correction['presence'] = 'on_stage'
    replies = [
        {'approved': False, 'reason': '发声主体应在场', 'correction': None},
        {'approved': False, 'reason': '修订参与方式', 'correction': {'actors': [correction]}},
        {'approved': True, 'reason': '测试边界最终确认', 'correction': None}]
    prompts = []

    def ask(parts, *args, **kwargs):
        prompt = parts[0]['text']; prompts.append(prompt)
        payload = json.loads(prompt.split('\n', 1)[1])
        assert payload['evidence']['current_segments'] == [
            {'paragraph': i, **row} for i, row in enumerate(segments, 1)]
        assert '1' not in payload['evidence']['source']
        assert [a['count'] for a in payload['candidate']['actors']] == [1, 2]
        assert {b['source_id']: b['entity_id'] for b in payload['candidate']['bindings']} == {1: 'e034', 2: 'UNKNOWN'}
        return deepcopy(replies[len(prompts) - 1])

    monkeypatch.setattr(client, 'ask_json', ask)
    result, receipt = REAL_BINDINGS(tmp_path / 'book/book_1', cleaned, catalog, segments, mapped)
    assert receipt['approved'] and result['actors'][0]['presence'] == 'on_stage'
    assert result['actors'][0]['count'] == 0 and result['actors'][1] == cleaned['actors'][1]
    assert '这次必须给出 correction' in prompts[1]
    assert '这次必须给出 correction' not in prompts[2]
    assert (records, bible, history, segments, raw) == before
    assert catalog.records == records and catalog.claims == history
