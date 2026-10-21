"""The reviewer must use the same paragraph coordinates as the source reader."""
from copy import deepcopy
import json

import pytest

from novel_manga.application.identity import semantic_review
from novel_manga.story.catalog import IdentityCatalog


# Other test modules isolate this service with an autouse fixture.
REAL_BINDINGS = semantic_review.bindings


@pytest.mark.parametrize('chapter', [1, 48])
def test_review_receives_numbered_segments_without_whole_neighbor_scenes(tmp_path, monkeypatch, chapter):
    episode = tmp_path / f'book/book_{chapter}'
    segments = [{'segment_id': 's1', 'text': '甲来了。\n他坐下。'},
                {'segment_id': 's2', 'text': '甲笑了。\n他离开。'}]
    answer = {'actors': []}
    before = deepcopy((segments, answer))
    sources = {str(chapter): '\n'.join(s['text'] for s in segments),
               str(chapter + 1): '下一章原文背景。'}
    monkeypatch.setattr(semantic_review, 'context_sources', lambda *a, **kw: sources)
    captured = []

    def ask(parts, schema, **kwargs):
        prompt = parts[0]['text']
        payload = json.loads(prompt.split('\n', 1)[1])
        captured.append((prompt, payload))
        assert schema['properties']['correction']['anyOf'][0]['properties'][
            'bindings']['items']['properties']['paragraphs']['items']['maximum'] == 2
        return {'approved': True, 'reason': '测试边界回复', 'correction': None}

    monkeypatch.setattr(semantic_review.client, 'ask_json', ask)
    catalog = IdentityCatalog({'characters': []}, [], {}, {}, [], {})
    result, review = REAL_BINDINGS(episode, answer, catalog, segments, {'mentions': []})
    prompt, payload = captured[0]
    assert payload['evidence']['current_segments'] == [
        {'paragraph': i, **s} for i, s in enumerate(segments, 1)]
    assert 'source' not in payload['evidence'] and sources[str(chapter + 1)] not in prompt
    assert '不能把章节号或自然行号当成本章paragraphs' in prompt
    assert result == {'actors': [], 'bindings': []} and review['approved']
    assert len(captured) == 1 and (segments, answer) == before
    assert str(chapter) in sources
