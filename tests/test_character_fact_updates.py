"""Source facts survive design; explicit corrections preserve unrelated production data."""
import json

import pytest

from novel_manga.application.review import bible
from novel_manga.application.review import character_updates
from novel_manga.models.bible import Character, StoryBible


def book(characters=()):
    return StoryBible(novel_title='测试', genre='玄幻', visual_style='3D', palette='蓝',
                      style_fingerprint='keep', characters=list(characters),
                      locations=['大殿：烛火照明'], continuity_rules=['保留原记录'])


@pytest.mark.parametrize('name,source,appearance,wardrobe', [
    ('科查', '科查走到林齐面前。他的脸一直到胸脯上都是一块斑驳的疤痕。鼻子和耳朵只剩黑洞。',
     '满面斑驳疤痕，鼻耳毁损只剩黑洞', '破洞药渍黑袍'),
    ('艾尔哈姆', '坐在椅上的是金发金眸的俊美青年。亚瑟说道：艾尔哈姆阁下。',
     '金发金眸、白肤俊美青年', '简单黑袍'),
    ('云帝', '形如七八岁孩童的云帝盘坐亭中。', '七八岁孩童外形', '长袍'),
])
def test_growth_receives_confirmed_reading_appearance_and_keeps_permanent_features(
        tmp_path, monkeypatch, name, source, appearance, wardrobe):
    facts = {name: {'name': name, 'appearance': appearance, 'wardrobe': wardrobe,
                    'evidence': [source]}}
    (tmp_path / 'reading_cast.json').write_text(json.dumps(
        {'characters': [name], 'character_facts': facts}, ensure_ascii=False))
    seen = []

    def reply(parts, schema, **kwargs):
        seen.append(parts[0]['text'])
        return {'characters': [{'name': name, 'same_as': '', 'confidence': 1,
                                'appearance': appearance, 'wardrobe': wardrobe,
                                'hair': '按原文记录', 'palette': '与原文服色一致'}]}

    monkeypatch.setattr(bible.model_client, 'ask_json', reply)
    result, added, refused, _ = bible.fill_characters(book(), tmp_path / 'story_bible.json',
        {name: {'mentions': 3, 'chapters': [1]}}, source, [name])
    assert added == [name] and refused == {}
    assert result.characters[0].appearance == appearance
    assert result.characters[0].wardrobe == result.characters[0].base_costume == wardrobe
    assert appearance in seen[0] and '持久毁容' in seen[0]
    assert '伤痕、表情' not in seen[0]


def test_design_context_is_identity_information_not_other_peoples_appearance(tmp_path, monkeypatch):
    existing = Character(name='豪森', role='少将', appearance='豪森独有的杀气死气模板', wardrobe='军装')
    seen = []
    monkeypatch.setattr(bible.model_client, 'ask_json', lambda parts, *a, **k: seen.append(parts[0]['text']) or {
        'characters': [{'name': '沃图', 'same_as': '', 'confidence': 1,
                        'appearance': '高大中年军官', 'wardrobe': '银甲'}]})
    bible.fill_characters(book([existing]), tmp_path / 'story_bible.json',
                          {'沃图': {'mentions': 3, 'chapters': [66]}}, '沃图身材高大，相貌堂堂。', ['沃图'])
    assert '豪森' in seen[0] and '少将' in seen[0]
    assert '豪森独有的杀气死气模板' not in seen[0]


def test_new_fill_keeps_explicit_male_gender_when_the_model_replies_female(tmp_path, monkeypatch):
    from novel_manga.story.reading_facts import facts_from_export
    facts = facts_from_export({'characters': [{'name': '科查', 'gender': '男',
        'appearance': '瘦削毁容的秘药大师', 'wardrobe': '破洞黑袍',
        'evidence': [{'chapter': 13, 'quote': '科查的面孔整个被毁掉了'}]}]})
    (tmp_path / 'reading_cast.json').write_text(json.dumps(
        {'characters': ['科查'], 'character_facts': facts}, ensure_ascii=False))
    seen = []

    def reply(parts, *_args, **_kwargs):
        seen.append(parts[0]['text'])
        return {'characters': [{'name': '科查', 'gender': '女', 'same_as': '', 'confidence': 1,
                                'appearance': '瘦削毁容的秘药大师', 'wardrobe': '破洞黑袍'}]}

    monkeypatch.setattr(bible.model_client, 'ask_json', reply)
    result, added, refused, _ = bible.fill_characters(book(), tmp_path / 'story_bible.json',
        {'科查': {'mentions': 3, 'chapters': [13]}}, '科查的面孔整个被毁掉了。', ['科查'])
    assert added == ['科查'] and refused == {}
    assert result.characters[0].gender == '男'
    assert '"gender": "男"' in seen[0] and '有值的性别' in seen[0]


def test_marked_design_for_an_unwritten_look_is_not_rejected_as_a_placeholder(tmp_path, monkeypatch):
    appearance = '原文未详述外貌；美术设计推断：圆润脸型、浅褐短发'
    monkeypatch.setattr(bible.model_client, 'ask_json', lambda *a, **k: {'characters': [{
        'name': '布朗侯爵', 'same_as': '', 'confidence': 1,
        'appearance': appearance, 'wardrobe': '校长礼服'}]})
    result, added, refused, _ = bible.fill_characters(book(), tmp_path / 'story_bible.json',
        {'布朗侯爵': {'mentions': 3, 'chapters': [34]}}, '布朗侯爵是校长。', ['布朗侯爵'])
    assert added == ['布朗侯爵'] and not refused
    assert result.characters[0].appearance == appearance


def test_excerpts_keep_described_then_named_subject_and_later_reply():
    text = '一名白金长发、黑蓝眸的俊美青年走入大殿。他穿着淡金长袍。云森浅浅一笑。林齐看向他。'
    assert text == bible.excerpts(text, '云森')


def test_explicit_update_writes_only_source_supported_selected_fields(tmp_path, monkeypatch):
    original = book([Character(name='科查', appearance='清瘦完整面孔', wardrobe='学者袍', voice_profile_id='fixed'),
                     Character(name='妙闻', appearance='光头老人', wardrobe='僧袍')]).model_dump()
    original['characters'][0]['local_note'] = '保留本地信息'
    original['local_top'] = {'budget': 7}
    path = tmp_path / 'story_bible.json'
    path.write_text(json.dumps(original, ensure_ascii=False))
    (tmp_path / 'bible_review.json').write_text(json.dumps({'flags': ['keep']}))
    source = '科查的面孔满是斑驳疤痕，鼻子和耳朵只剩黑洞。'
    monkeypatch.setattr(character_updates, 'ask_json', lambda *a, **k: {'characters': [{
        'name': '科查', 'changes': {'appearance': '满面斑驳疤痕，鼻耳毁损只剩黑洞'},
        'evidence': [source], 'reason': '永久毁容'}]})
    result = bible.reconcile_characters(tmp_path, ['科查'], source=source)
    after = json.loads(path.read_text())
    assert result['updated'] == ['科查'] and not result['rejected']
    assert after['characters'][0]['appearance'] == '满面斑驳疤痕，鼻耳毁损只剩黑洞'
    after['characters'][0]['appearance'] = original['characters'][0]['appearance']
    assert after == original
    assert json.loads((tmp_path / 'bible_review.json').read_text())['flags'] == ['keep']


def test_failed_or_unsupported_patch_does_not_block_other_selected_character(tmp_path, monkeypatch):
    initial = book([Character(name='多闻尊者', appearance='老人', wardrobe='旧衣'),
                    Character(name='妙闻', appearance='光头老人', wardrobe='僧袍')]).model_dump()
    (tmp_path / 'story_bible.json').write_text(json.dumps(initial, ensure_ascii=False))
    source = '妙闻发令。坐在妙闻身边骨瘦如柴的多闻尊者站起。'
    monkeypatch.setattr(character_updates, 'ask_json', lambda *a, **k: {'characters': [
        {'name': '多闻尊者', 'changes': {'appearance': '骨瘦如柴的老人'},
         'evidence': ['坐在妙闻身边骨瘦如柴的多闻尊者站起。'], 'reason': '独立人物'},
        {'name': '妙闻', 'changes': {'name': '多闻尊者'}, 'evidence': [source], 'reason': '合并'},
    ]})
    result = bible.reconcile_characters(tmp_path, ['多闻尊者', '妙闻'], source=source)
    after = json.loads((tmp_path / 'story_bible.json').read_text())
    assert result['updated'] == ['多闻尊者'] and '妙闻' in result['rejected']
    assert after['characters'][1] == initial['characters'][1]
    assert [c['name'] for c in after['characters']] == ['多闻尊者', '妙闻']


@pytest.mark.parametrize('changes,evidence', [
    ({'appearance': '一张新脸'}, ['不存在的原文']),
    ({'voice_profile_id': 'different'}, ['云帝是孩童。']),
    ({'appearance': ''}, ['云帝是孩童。']),
])
def test_rejected_update_keeps_original_file_bytes(tmp_path, monkeypatch, changes, evidence):
    path = tmp_path / 'story_bible.json'
    path.write_text(book([Character(name='云帝', appearance='孩童', wardrobe='长袍')]).model_dump_json())
    before = path.read_bytes()
    monkeypatch.setattr(character_updates, 'ask_json', lambda *a, **k: {'characters': [{
        'name': '云帝', 'changes': changes, 'evidence': evidence, 'reason': 'test'}]})
    result = bible.reconcile_characters(tmp_path, ['云帝'], source='云帝是孩童。')
    assert result['rejected'] and path.read_bytes() == before


def test_no_change_update_preserves_confirmed_manual_design_without_redraw(tmp_path, monkeypatch):
    path = tmp_path / 'story_bible.json'
    path.write_text(book([Character(name='云帝', age='外貌形如孩童',
                                    appearance='粉嫩孩童，细小白嫩手指', wardrobe='长袍')]).model_dump_json())
    before = path.read_bytes()
    seen = []
    monkeypatch.setattr(character_updates, 'ask_json', lambda parts, *a, **k: seen.append(parts[0]['text']) or {
        'characters': [{'name': '云帝', 'changes': {}, 'evidence': [], 'reason': '现设定符合原文'}]})
    result = bible.reconcile_characters(tmp_path, ['云帝'], source='形如七八岁孩童的云帝。')
    assert result['unchanged'] == ['云帝'] and path.read_bytes() == before
    assert '已确认的人工修正' in seen[0] and '阶段状态' in seen[0]


@pytest.mark.parametrize('malformed', [None, 'wrong shape', {'name': []}])
def test_malformed_model_rows_do_not_abort_valid_selected_updates(tmp_path, monkeypatch, malformed):
    initial = book([Character(name='科查', appearance='普通脸', wardrobe='黑袍'),
                    Character(name='云帝', appearance='孩童', wardrobe='长袍')]).model_dump()
    path = tmp_path / 'story_bible.json'
    path.write_text(json.dumps(initial, ensure_ascii=False))
    source = '科查满脸疤痕。'
    valid = {'name': '科查', 'changes': {'appearance': '满脸疤痕'}, 'evidence': [source], 'reason': '永久外貌'}
    monkeypatch.setattr(character_updates, 'ask_json', lambda *a, **k: {'characters': [malformed, valid]})
    result = bible.reconcile_characters(tmp_path, ['科查', '云帝'], source=source)
    assert result['updated'] == ['科查'] and '云帝' in result['rejected']
    assert json.loads(path.read_text())['characters'][1] == initial['characters'][1]


def test_null_model_character_list_is_reported_without_touching_bible(tmp_path, monkeypatch):
    path = tmp_path / 'story_bible.json'
    path.write_text(book([Character(name='科查', appearance='疤痕', wardrobe='黑袍')]).model_dump_json())
    before = path.read_bytes()
    monkeypatch.setattr(character_updates, 'ask_json', lambda *a, **k: {'characters': None})
    result = bible.reconcile_characters(tmp_path, ['科查'], source='科查满脸疤痕。')
    assert '科查' in result['rejected'] and path.read_bytes() == before
