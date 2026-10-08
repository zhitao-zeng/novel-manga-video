"""A sibling chapter's new design must invalidate this chapter's UNKNOWN binding."""
import json
import sys

import pytest

from novel_manga.application.planning import cli, requests
from novel_manga.application.review import bible
from novel_manga.llm import client
from novel_manga.planning.context import PlannerContext


def test_catalogue_is_reloaded_even_when_growth_added_nothing_locally(tmp_path, monkeypatch):
    source = tmp_path / 'source.md'
    source.write_text('第一章 来客\n' + '主角看见新客走进庭院。\n' * 100)
    novel = tmp_path / 'outputs/book'
    novel.mkdir(parents=True)
    path = novel / 'story_bible.json'
    document = {'novel_title': '测试', 'genre': '通用', 'visual_style': '3D', 'palette': '冷蓝',
                'style_fingerprint': 'test', 'characters': [
                    {'name': '主角', 'role': '主角', 'appearance': '黑发', 'wardrobe': '外套'}],
                'locations': ['庭院：空旷院落']}
    path.write_text(json.dumps(document))
    (novel / 'reading_cast.json').write_text(json.dumps({'characters': ['主角', '新客']}))
    monkeypatch.setattr(client, 'ask_json', lambda *a, **k: {'actors': [
        {'source_id': 1, 'name': '新客', 'forms': [{'form': '新客', 'kind': 'proper', 'paragraphs': [1]}],
         'paragraphs': [1], 'kind': 'individual', 'count': 1, 'presence': 'on_stage', 'appearance': ''}]})

    def sibling_added_the_person(*args):
        current = json.loads(path.read_text())
        current['characters'].append({'name': '新客', 'appearance': '灰发', 'wardrobe': '长外套'})
        path.write_text(json.dumps(current))
        return []
    monkeypatch.setattr(bible, 'grow_unbound_roster', sibling_added_the_person)
    offered = []
    class BoundEnough(RuntimeError):
        pass
    def creation(**kwargs):
        offered.extend(c['name'] for c in kwargs['payload']['story_bible']['characters'])
        raise BoundEnough
    monkeypatch.setattr(requests, 'call_model', creation)
    monkeypatch.setattr(sys, 'argv', ['plan_chapter_thin.py', str(source), '--novel-id', 'book',
        '--title', '测试', '--bible', str(path), '--output-root', str(novel.parent)])
    with pytest.raises(BoundEnough):
        cli.main(context=PlannerContext.from_env())
    assert offered == ['新客']
