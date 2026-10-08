from novel_manga.application.identity import reading_locations
from novel_manga.planning.places import offered_locations
from novel_manga.util import atomic_write_json, read_json


def test_adoption_preserves_old_ids_and_chapter_evidence(tmp_path):
    atomic_write_json(tmp_path/'story_bible.json', {'characters': [], 'locations': ['敦尔刻：旧城', '大学城：旧图']})
    atomic_write_json(tmp_path/'reading_cast.json', {'characters': ['林齐'], 'chapters': [1]})
    exported = {'locations': [{'name': '敦尔刻海边沙滩', 'description': '林齐站在沙滩上',
                              'first_chapter': 531, 'chapters': [531, 532]}]}
    assert reading_locations.adopt(tmp_path, exported) == ['敦尔刻海边沙滩']
    assert reading_locations.adopt(tmp_path, exported) == []
    assert read_json(tmp_path/'story_bible.json')['locations'] == ['敦尔刻：旧城', '大学城：旧图', '敦尔刻海边沙滩']
    reading = read_json(tmp_path/'reading_cast.json')
    assert reading['characters'] == ['林齐'] and reading['chapters'] == [1]
    assert reading['locations'] == exported['locations']
    assert read_json(tmp_path/'bible_growth.json')['531']['locations'] == ['敦尔刻海边沙滩']


def test_descriptive_scene_is_offered_from_reading_even_without_literal_name():
    actual = '云氏一族海底平原：珊瑚建筑'
    later = '未来场景：宫殿'
    offered = offered_locations(['大学城：旧图', actual, later], chapter=460,
        named_here=lambda name: False, recent=set(), added_at={'云氏一族海底平原': 453, '未来场景': 600},
        window=3, read_here={'云氏一族海底平原', '未来场景'})
    assert actual in offered and later not in offered


def test_location_design_uses_registered_chapter_not_name_search(tmp_path, monkeypatch):
    atomic_write_json(tmp_path/'story_bible.json', {'locations': ['海底平原']})
    atomic_write_json(tmp_path/'reading_cast.json', {'locations': [
        {'name': '海底平原', 'first_chapter': 453, 'chapters': [453], 'description': '人群站在海底'}]})
    atomic_write_json(tmp_path/'novel.json', {'novel_id': 'book'})
    calls = []
    monkeypatch.setattr(reading_locations, 'chapter_text', lambda metadata, chapter: f'正文{chapter}：魔法穹顶下的珊瑚')
    def ask(parts, schema, **kwargs):
        calls.append(parts)
        assert '正文453' in str(parts) and '人群站在海底' in str(parts)
        return {'grounded': True, 'description': '空旷的珊瑚平原，蓝色穹顶，日间散射光。'}
    monkeypatch.setattr(reading_locations, 'ask_json', ask)
    reading_locations.ensure_designs(tmp_path, {'location_001'})
    assert '空旷' in read_json(tmp_path/'story_bible.json')['locations'][0]
    reading_locations.ensure_designs(tmp_path, {'location_001'})
    assert len(calls) == 1
