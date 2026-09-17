import json

import pytest

from novel_manga.models import Character, StoryBible
import novel_manga.llm.client as model_client
import novel_manga.story.identity as story_names
import review_bible_thin as review_bible


@pytest.mark.parametrize('name, known, expected', [
    ('吴龙', ['吴', '龙'], False),
    ('雷啸天', ['雷啸', '天'], False),
    ('问天', ['南宫问天', '天'], False),
    ('大厅外', ['大厅'], False),
    ('吴\n龙', ['吴龙'], True),
    ('吴龙', ['吴\n龙'], True),
])
def test_containment_cannot_claim_a_missing_asset_exists(name, known, expected):
    assert story_names.name_matches(name, known) is expected


def test_growth_registers_distinct_actor_but_reuses_an_explicit_alias(tmp_path, monkeypatch):
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='2d', palette='蓝', style_fingerprint='test',
                       characters=[Character(name=n, appearance='已有角色', wardrobe='旧衣') for n in ['吴', '龙', '南宫问天']],
                       locations=['大厅：石柱'])
    (tmp_path / 'story_bible.json').write_text(bible.model_dump_json())
    # Confirmed aliases remain reusable; removing substring equality must not
    # duplicate somebody already linked to an existing canonical entry.
    (tmp_path / 'bible_aliases.json').write_text(json.dumps({'问天': '南宫问天'}))
    calls = []
    def ask(parts, schema, **kwargs):
        calls.append(kwargs['name'])
        assert '【吴龙】' in parts[0]['text'] and '【问天】' not in parts[0]['text']
        return {'characters': [{'name': '吴龙', 'same_as': '', 'confidence': 1,
                                'appearance': '瘦高男子', 'wardrobe': '绿袍'}]}
    monkeypatch.setattr(model_client, 'ask_json', ask)
    scan = {'names': [{'name': n, 'kind': '具名角色', 'mentions': 3, 'speaks_or_close_up': True}
                      for n in ['吴龙', '问天']], 'locations': []}
    result = review_bible.grow_bible(tmp_path, '吴龙穿着绿袍，向问天点头。', 1, scan)
    assert result['characters'] == ['吴龙'] and calls == ['fill']
    after = json.loads((tmp_path / 'story_bible.json').read_text())
    assert [c['name'] for c in after['characters']] == ['吴', '龙', '南宫问天', '吴龙']
    # The newly added entry is found on the next chapter, with no model call.
    review_bible.grow_bible(tmp_path, '吴龙与问天交谈。', 2, scan)
    assert calls == ['fill']


def test_partial_same_as_cannot_bind_to_an_arbitrary_longer_name(tmp_path, monkeypatch):
    bible = StoryBible(novel_title='测试', genre='generic', visual_style='2d', palette='蓝', style_fingerprint='test',
                       characters=[Character(name='南宫问天', appearance='青年', wardrobe='白衣')], locations=[])
    (tmp_path / 'story_bible.json').write_text(bible.model_dump_json())
    monkeypatch.setattr(model_client, 'ask_json', lambda *a, **k: {'characters': [{
        'name': '陌生客', 'same_as': '问天', 'confidence': 1, 'appearance': '青年', 'wardrobe': '白衣'}]})
    review_bible.grow_bible(tmp_path, '陌生客出现了。', 1, {'names': [{
        'name': '陌生客', 'kind': '具名角色', 'mentions': 3, 'speaks_or_close_up': True}], 'locations': []})
    assert not (tmp_path / 'bible_aliases.json').exists()
