import json

import httpx
import pytest

from novel_manga.bible import BibleBuilder
from novel_manga.config import Settings
from novel_manga.ingest import read_novel


def test_initial_bible_repairs_invalid_response_with_original_source(tmp_path):
    source = tmp_path / 'book.txt'
    source.write_text('第一章 门前\n甲推开木门，走进庭院。')
    novel = read_novel(source, novel_id='test', title='测试')
    valid = {'novel_title': '测试', 'genre': '玄幻', 'visual_style': '国漫',
             'palette': '青', 'style_fingerprint': '',
             'characters': [{'name': '甲', 'appearance': '黑发青年', 'wardrobe': '青衣'}],
             'locations': ['庭院：青砖铺地的方形院落，三面木构回廊环抱，院心一口石砌水井，白天天光从头顶直落。']}
    requests = []

    def reply(request):
        payload = json.loads(request.content)
        requests.append(payload)
        data = {'invalid': True} if len(requests) == 1 else valid
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(data)}}]})

    builder = BibleBuilder(Settings(llm_base_url='http://example.invalid/v1', llm_api_key='test',
                                   planner_max_revisions=1))
    builder.client.close()
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        builder.client = client
        bible = builder.build_bible(novel)
    assert [c.name for c in bible.characters] == ['甲']
    assert bible.locations == ['庭院：青砖铺地的方形院落，三面木构回廊环抱，院心一口石砌水井，白天天光从头顶直落。'] and bible.style_fingerprint
    assert len(requests) == 2
    assert novel.text in requests[0]['messages'][1]['content']
    assert requests[0]['messages'][:2] == requests[1]['messages'][:2]
    assert '校验反馈' in requests[1]['messages'][-1]['content']


def test_initial_bible_stops_at_existing_retry_budget(tmp_path):
    source = tmp_path / 'book.txt'
    source.write_text('第一章 门前\n甲推开木门。')
    novel = read_novel(source, novel_id='test', title='测试')
    builder = BibleBuilder(Settings(planner_max_revisions=1))
    builder.client.close()
    calls = []
    builder._json = lambda *a, **k: calls.append(k) or {}
    with pytest.raises(ValueError, match='after 2 attempt'):
        builder.build_bible(novel)
    assert len(calls) == 2


def test_initial_bible_rejects_a_location_that_is_only_a_name(tmp_path):
    # The entry is pasted whole into the empty-scene card prompt, so a bare name leaves the image
    # model to invent the architecture, the hour and the light - and drops the no-people and
    # illegible-inscription rules the description is supposed to carry.
    source = tmp_path / 'book.txt'
    source.write_text('第一章 门前\n甲推开木门，走进庭院。')
    novel = read_novel(source, novel_id='test', title='测试')
    bare = {'novel_title': '测试', 'genre': '玄幻', 'visual_style': '国漫',
            'palette': '青', 'style_fingerprint': '',
            'characters': [{'name': '甲', 'appearance': '黑发青年', 'wardrobe': '青衣'}],
            'locations': ['庭院']}

    def reply(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(bare)}}]})

    builder = BibleBuilder(Settings(llm_base_url='http://example.invalid/v1', llm_api_key='test',
                                    planner_max_revisions=0))
    builder.client.close()
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        builder.client = client
        with pytest.raises(ValueError, match='locations.0'):
            builder.build_bible(novel)
