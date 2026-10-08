"""Whole-book casting decisions survive chapter design and bounded requests."""
from novel_manga.application.review import bible
from novel_manga.models.bible import StoryBible


def design(name):
    return {'name': name, 'same_as': '', 'confidence': 0.95, 'role': '配角',
            'gender': '男', 'age': '中年', 'appearance': '圆脸、灰色短发、矮胖身形',
            'wardrobe': '黑色外套、白衬衫', 'hair': '灰色短发', 'palette': '黑白',
            'base_costume': '黑色外套', 'signature_prop': ''}


def book():
    return StoryBible(novel_title='测试', genre='通用', visual_style='3D', palette='冷蓝',
                      style_fingerprint='test', characters=[])


def test_confirmed_cast_with_an_honorific_is_designed_without_another_identity_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(bible.model_client, 'ask_json', lambda *a, **k: {'characters': [design('格朗先生')]})
    missing = {'格朗先生': {'mentions': 4, 'chapters': [14]}}
    result, filled, refused, _ = bible.fill_characters(book(), tmp_path / 'bible.json', missing,
        '格朗先生坐在桌后。', ['格朗先生'])
    assert filled == ['格朗先生'] and refused == {}
    assert result.characters[0].name == '格朗先生'


def test_unconfirmed_appellation_still_needs_an_identity_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(bible.model_client, 'ask_json', lambda *a, **k: {'characters': [design('青年男子')]})
    _, filled, refused, _ = bible.fill_characters(book(), tmp_path / 'bible.json',
        {'青年男子': {'mentions': 4, 'chapters': [5]}}, '青年男子走来。')
    assert filled == [] and '称呼类人物' in refused['青年男子']


def test_many_missing_designs_are_batched_and_cannot_repeat_the_existing_cast(tmp_path, monkeypatch):
    calls = []
    def reply(parts, schema, **kwargs):
        requested = schema['properties']['characters']
        names = [row['properties']['name']['enum'][0] for row in requested['prefixItems']]
        calls.append(names)
        assert requested['items'] is False
        assert requested['minItems'] == requested['maxItems'] == len(names)
        return {'characters': [design(name) for name in names]}
    monkeypatch.setattr(bible.model_client, 'ask_json', reply)
    names = [f'人物{i}' for i in range(9)]
    result, filled, refused, _ = bible.fill_characters(book(), tmp_path / 'bible.json',
        {name: {'mentions': 4, 'chapters': [1]} for name in names}, '、'.join(names), names)
    assert calls == [names[:4], names[4:8], names[8:]]
    assert filled == names and len(result.characters) == 9 and not refused
