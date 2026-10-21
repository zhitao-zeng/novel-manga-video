"""Object scope contracts and data preservation; simulated replies are not a recall score."""
from copy import deepcopy
import json

import pytest

from novel_manga.application.identity import flow, semantic_review
from novel_manga.llm import client
from novel_manga.util import atomic_write_json


REAL_BINDINGS = semantic_review.bindings
SOURCE_RULE = ('同名或同类物件不等于同一件；当前事件与回忆、转述中的具体物件，只有原文明确支持同一性才共用主体，'
               '否则分别登记各自指称和段落，过去仅被提及的物件标mentioned；泛指类名不强行建成具体物件。')
REVIEW_RULE = ('核对物件各次指称的事件和时间：不能仅因同名同类就把回忆、转述中的物件归入当前物件。'
               '同一性有明确原文依据才合并，否则保留独立主体及各自的指称、段落和presence；泛指类名无需新增主体。')


def actor(sid, name, kind, presence, forms, paragraphs):
    return {'source_id': sid, 'name': name, 'kind': kind, 'presence': presence,
            'count': 1, 'appearance': '', 'paragraphs': paragraphs,
            'forms': [{'form': text, 'kind': 'contextual', 'paragraphs': refs} for text, refs in forms]}


@pytest.mark.parametrize('explicit_same_object', [False, True])
def test_existing_entrypoint_preserves_distinct_or_explicitly_same_object_scope(
        tmp_path, monkeypatch, explicit_same_object):
    book = tmp_path / 'book'
    episode = book / 'book_90'
    records = {
        'story_bible.json': {'characters': [{'name': '甲', 'appearance': '原设计'}]},
        'entity/entities.json': [{'id': 'e071', 'canonical': '甲', 'asset_id': 'character_023'},
                                 {'id': 'e099', 'canonical': '旧甲', 'merged_into': 'e071'}],
        'entity/claims.json': [{'type': 'same_as', 'subject': 'e099', 'object': 'e071', 'status': 'accepted', 'chapter': 1}],
        'entity/corrections.json': [{'subject': 'e099', 'object': 'e071', 'decision': 'same'}],
        'entity/merges.json': [{'from': 'e099', 'into': 'e071'}],
        'reading_cast.json': {'characters': ['甲'], 'aliases': {}},
        'series_assets/phases.json': {'characters': {'甲': {'asset_id': 'original_phase'}}},
        'series_assets/manifest.json': {'characters': {'甲': {'asset_id': 'character_023'}}},
    }
    for rel, value in records.items():
        atomic_write_json(book / rel, value)
    second = ('车子继续行驶。甲想起从前坐过这辆出租车，正是他现在乘坐的同一辆车。'
              if explicit_same_object else '车子继续行驶。甲想起自己曾坐上一辆出租车去外地。')
    segments = [{'segment_id': 's1', 'text': '甲坐在出租车里。'}, {'segment_id': 's2', 'text': second}]
    atomic_write_json(episode / 'segments.json', segments)
    before = {p: p.read_bytes() for p in [*[book / rel for rel in records], episode / 'segments.json']}
    current_forms = [('出租车', [1, 2] if explicit_same_object else [1]), ('车子', [2])]
    actors = [actor(1, '甲', 'individual', 'on_stage', [('甲', [1, 2])], [1, 2]),
              actor(2, '出租车', 'object', 'on_stage', current_forms, [1, 2])]
    if not explicit_same_object:
        actors.append(actor(3, '一辆出租车', 'object', 'mentioned', [('一辆出租车', [2])], [2]))
    original = deepcopy(actors)
    calls = []

    def ask(parts, *args, **kwargs):
        name = kwargs['name']; calls.append(name); prompt = parts[0]['text']
        if name == 'chapter_identity_source':
            assert SOURCE_RULE in prompt
            assert kwargs['max_tokens'] == 4096 and kwargs['timeout'] == 240
            return {'source_readable': True, 'source_problem': '', 'actors': deepcopy(actors)}
        assert name == 'chapter_binding_review'
        assert REVIEW_RULE in prompt
        payload = json.loads(prompt.split('\n', 1)[1])
        assert payload['candidate']['actors'] == actors
        # Object extras already have this binding before review. A fabricated
        # rejection of an identical correction must not buy a second judgment.
        assert payload['candidate']['bindings'][1]['decision'] == 'extra'
        return {'approved': True, 'reason': '本地接口测试回复', 'correction': None}

    monkeypatch.setattr(client, 'ask_json', ask)
    monkeypatch.setattr(semantic_review, 'bindings', REAL_BINDINGS)
    result = flow.resolve_chapter(episode)
    assert calls == ['chapter_identity_source', 'chapter_binding_review']
    assert result['source_actors'] == actors == original
    assert {m['entity_id'] for m in result['mentions'] if m['source_actor'] == 1} == {'e071'}
    current_mentions = [m for m in result['mentions'] if m['source_actor'] == 2]
    assert all(m['presence'] == 'on_stage' and m['entity_id'] == 'UNKNOWN' for m in current_mentions)
    if not explicit_same_object:
        past_mentions = [m for m in result['mentions'] if m['source_actor'] == 3]
        assert past_mentions and all(m['presence'] == 'mentioned' and m['entity_id'] == 'UNKNOWN' for m in past_mentions)
        assert all(m['paragraphs'] == [2] for m in past_mentions)
        assert next(m for m in current_mentions if m['form'] == '出租车')['paragraphs'] == [1]
    else:
        assert len([a for a in result['source_actors'] if a['kind'] == 'object']) == 1
    assert result['semantic_new_names'] == [] and result['semantic_extras'] == ['出租车']
    assert before == {p: p.read_bytes() for p in before}
    cached = (episode / 'identity_context.json').read_bytes()
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: pytest.fail('An adopted unchanged chapter was reopened'))
    assert flow.resolve_chapter(episode) == result
    assert (episode / 'identity_context.json').read_bytes() == cached
    assert before == {p: p.read_bytes() for p in before}
