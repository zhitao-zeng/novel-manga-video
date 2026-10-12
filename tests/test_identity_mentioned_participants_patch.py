"""Check the extraction contract and data preservation, without evaluating a model."""
from copy import deepcopy

import pytest

from novel_manga.application.identity import flow, semantic_review
from novel_manga.llm import client
from novel_manga.util import atomic_write_json, read_json


REAL_BINDINGS = semantic_review.bindings
SCOPE_SENTENCE = ('对话或回忆中提到的、实际参与过具体事件的无名个体也要列出，'
                  '使用原文称谓，未在当前现场出现则标为mentioned；'
                  '不要把泛指职业或假设的人物当成具体个体。')


@pytest.mark.parametrize('reply_includes_unnamed_actor', [False, True])
def test_existing_flow_preserves_a_reported_unnamed_actor_and_prior_data(
        tmp_path, monkeypatch, reply_includes_unnamed_actor):
    book = tmp_path / 'book'
    episode = book / 'book_1'
    fixtures = {
        'story_bible.json': {'characters': [{'name': '甲', 'appearance': '原设计'}]},
        'entity/entities.json': [
            {'id': 'e071', 'canonical': '甲', 'asset_id': 'character_023'},
            {'id': 'e099', 'canonical': '旧甲', 'merged_into': 'e071'}],
        'entity/claims.json': [{'type': 'same_as', 'subject': 'e099', 'object': 'e071', 'status': 'accepted', 'chapter': 1}],
        'entity/corrections.json': [{'subject': 'e099', 'object': 'e071', 'decision': 'same'}],
        'entity/merges.json': [{'from': 'e099', 'into': 'e071'}],
        'reading_cast.json': {'characters': ['甲'], 'aliases': {}},
        'series_assets/phases.json': {'characters': {'甲': {'asset_id': 'original_phase'}}},
        'series_assets/manifest.json': {'characters': {'甲': {'asset_id': 'character_023'}}},
    }
    for rel, value in fixtures.items():
        atomic_write_json(book / rel, value)
    segments = [{'segment_id': 's1', 'text': '甲走进屋里。'},
                {'segment_id': 's2', 'text': '甲说：“昨天送货的人把箱子放在门口。”如果能找到医生就好了。'}]
    atomic_write_json(episode / 'segments.json', segments)
    protected = {p: p.read_bytes() for p in [*[book / rel for rel in fixtures], episode / 'segments.json']}

    def actor(sid, name, paragraph, presence):
        return {'source_id': sid, 'name': name, 'kind': 'individual', 'count': 1,
                'presence': presence, 'appearance': '', 'paragraphs': [paragraph],
                'forms': [{'form': name, 'kind': 'contextual', 'paragraphs': [paragraph]}]}

    actors = [actor(1, '甲', 1, 'on_stage')]
    if reply_includes_unnamed_actor:
        actors.append(actor(2, '送货的人', 2, 'mentioned'))
    source_reply = {'source_readable': True, 'source_problem': '', 'actors': actors}
    before = deepcopy(source_reply)
    calls = []

    def ask(parts, *args, **kwargs):
        name = kwargs['name']; calls.append(name)
        if name == 'chapter_identity_source':
            assert parts[0]['text'].count(SCOPE_SENTENCE) == 1
            assert kwargs['max_tokens'] == 4096 and kwargs['timeout'] == 240
            return deepcopy(source_reply)
        assert name == 'chapter_binding_review'
        return {'approved': True, 'reason': '测试边界回复', 'correction': None}

    monkeypatch.setattr(client, 'ask_json', ask)
    monkeypatch.setattr(semantic_review, 'bindings', REAL_BINDINGS)
    result = flow.resolve_chapter(episode)
    assert calls == ['chapter_identity_source', 'chapter_binding_review']
    assert result['source_actors'] == actors
    assert {row['source_actor']: row['entity_id'] for row in result['mentions']} == (
        {1: 'e071', 2: 'UNKNOWN'} if reply_includes_unnamed_actor else {1: 'e071'})
    assert result['semantic_new_names'] == result['semantic_extras'] == []
    assert not any(row['form'] == '医生' for row in result['mentions'])
    assert source_reply == before
    assert protected == {p: p.read_bytes() for p in protected}
    assert read_json(episode / 'identity_context.json', {}) == result

    # The new prompt must not reopen the just-adopted, unchanged chapter cache.
    cached_bytes = (episode / 'identity_context.json').read_bytes()
    monkeypatch.setattr(client, 'ask_json', lambda *a, **kw: pytest.fail('An unchanged adopted chapter was reopened'))
    assert flow.resolve_chapter(episode) == result
    assert (episode / 'identity_context.json').read_bytes() == cached_bytes
    assert protected == {p: p.read_bytes() for p in protected}
